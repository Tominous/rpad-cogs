import asyncio
from builtins import filter, map
from collections import OrderedDict
from collections import defaultdict
import os
import io
import json
import re
import traceback
import urllib.parse

from dateutil import tz
import discord
from discord.ext import commands
from enum import Enum
import prettytable

from __main__ import user_allowed, send_cmd_help

from . import dadguide
from . import rpadutils
from .rpadutils import *
from .rpadutils import CogSettings
from .utils import checks
from .utils.chat_formatting import *
from .utils.dataIO import dataIO

HELP_MSG = """
^helpid : shows this message
^id <query> : look up a monster and print a link to puzzledragonx
^pic <query> : Look up a monster and display its image inline

Options for <query>
    <id> : Find a monster by ID
        ^id 1234 (picks sun quan)
    <name> : Take the best guess for a monster, picks the most recent monster
        ^id kali (picks uvo d kali)
    <prefix> <name> : Limit by element or awoken, e.g.
        ^id ares  (selects the most recent, awoken ares)
        ^id aares (explicitly selects awoken ares)
        ^id a ares (spaces work too)
        ^id rd ares (select a specific evo for ares, the red/dark one)
        ^id r/d ares (slashes, spaces work too)

computed nickname list and overrides: https://docs.google.com/spreadsheets/d/1EyzMjvf8ZCQ4K-gJYnNkiZlCEsT9YYI9dUd-T5qCirc/pubhtml
submit an override suggestion: https://docs.google.com/forms/d/1kJH9Q0S8iqqULwrRqB9dSxMOMebZj6uZjECqi4t9_z0/edit"""

EMBED_NOT_GENERATED = -1

INFO_PDX_TEMPLATE = 'http://www.puzzledragonx.com/en/monster.asp?n={}'

MEDIA_PATH = 'https://f002.backblazeb2.com/file/dadguide-data/media/'
RPAD_PIC_TEMPLATE = MEDIA_PATH + 'portraits/{0:05d}.png'
RPAD_PORTRAIT_TEMPLATE = MEDIA_PATH + 'icons/{0:05d}.png'
VIDEO_TEMPLATE = MEDIA_PATH + 'animated_portraits/{0:05d}.mp4'
GIF_TEMPLATE = MEDIA_PATH + 'animated_portraits/{0:05d}.gif'
ORB_SKIN_TEMPLATE = MEDIA_PATH + 'orb_skins/{0:03d}.png'
ORB_SKIN_CB_TEMPLATE = MEDIA_PATH + 'orb_skins/{0:03d}cb.png'

YT_SEARCH_TEMPLATE = 'https://www.youtube.com/results?search_query={}'
SKYOZORA_TEMPLATE = 'http://pad.skyozora.com/pets/{}'


def get_pdx_url(m):
    return INFO_PDX_TEMPLATE.format(rpadutils.get_pdx_id(m))


def get_portrait_url(m):
    return RPAD_PORTRAIT_TEMPLATE.format(m.monster_id)


def get_pic_url(m):
    return RPAD_PIC_TEMPLATE.format(m.monster_id)


class IdEmojiUpdater(EmojiUpdater):
    def __init__(self, emoji_to_embed, m: dadguide.DgMonster = None,
                 pad_info=None, selected_emoji=None):
        self.emoji_dict = emoji_to_embed
        self.m = m
        self.pad_info = pad_info
        self.selected_emoji = selected_emoji

    def on_update(self, selected_emoji):
        if selected_emoji == self.pad_info.previous_monster_emoji:
            if self.m.prev_monster is None:
                return False
            self.m = self.m.prev_monster
        elif selected_emoji == self.pad_info.next_monster_emoji:
            if self.m.next_monster is None:
                return False
            self.m = self.m.next_monster
        else:
            self.selected_emoji = selected_emoji
            return True
        self.emoji_dict = self.pad_info.get_id_emoji_options(m=self.m)
        return True


class PadInfo:
    def __init__(self, bot):
        self.bot = bot

        self.settings = PadInfoSettings("padinfo")

        self.index_all = None
        self.index_na = None

        self.menu = Menu(bot)

        # These emojis are the keys into the idmenu submenus
        self.id_emoji = '\N{INFORMATION SOURCE}'
        self.evo_emoji = char_to_emoji('e')
        self.mats_emoji = char_to_emoji('m')
        self.ls_emoji = '\N{INFORMATION SOURCE}'
        self.left_emoji = char_to_emoji('l')
        self.right_emoji = char_to_emoji('r')
        self.pantheon_emoji = '\N{CLASSICAL BUILDING}'
        self.skillups_emoji = '\N{MEAT ON BONE}'
        self.pic_emoji = '\N{FRAME WITH PICTURE}'
        self.other_info_emoji = '\N{SCROLL}'
        self.previous_monster_emoji = '\N{HEAVY MINUS SIGN}'
        self.next_monster_emoji = '\N{HEAVY PLUS SIGN}'
        self.remove_emoji = self.menu.emoji['no']

        self.historic_lookups_file_path = "data/padinfo/historic_lookups.json"
        self.historic_lookups_file_path_id2 = "data/padinfo/historic_lookups_id2.json"
        if not dataIO.is_valid_json(self.historic_lookups_file_path):
            print("Creating empty historic_lookups.json...")
            dataIO.save_json(self.historic_lookups_file_path, {})

        if not dataIO.is_valid_json(self.historic_lookups_file_path_id2):
            print("Creating empty historic_lookups_id2.json...")
            dataIO.save_json(self.historic_lookups_file_path_id2, {})

        self.historic_lookups = dataIO.load_json(self.historic_lookups_file_path)
        self.historic_lookups_id2 = dataIO.load_json(self.historic_lookups_file_path_id2)

    def __unload(self):
        # Manually nulling out database because the GC for cogs seems to be pretty shitty
        self.index_all = None
        self.index_na = None
        self.historic_lookups = {}
        self.historic_lookups_id2 = {}

    async def reload_nicknames(self):
        await self.bot.wait_until_ready()
        while self == self.bot.get_cog('PadInfo'):
            try:
                await self.refresh_index()
                print('Done refreshing PadInfo')
            except Exception as ex:
                print("reload padinfo loop caught exception " + str(ex))
                traceback.print_exc()

            await asyncio.sleep(60 * 60 * 1)

    async def refresh_index(self):
        """Refresh the monster indexes."""
        dg_cog = self.bot.get_cog('Dadguide')
        await dg_cog.wait_until_ready()
        self.index_all = dg_cog.create_index()
        self.index_na = dg_cog.create_index(lambda m: m.on_na)

    def get_monster_by_no(self, monster_no: int):
        dg_cog = self.bot.get_cog('Dadguide')
        return dg_cog.get_monster_by_no(monster_no)

    @commands.command(pass_context=True)
    async def jpname(self, ctx, *, query: str):
        """Print the Japanese name of a monster"""
        m, err, debug_info = self.findMonster(query)
        if m is not None:
            await self.bot.say(monsterToHeader(m))
            await self.bot.say(box(m.name_jp))
        else:
            await self.bot.say(self.makeFailureMsg(err))

    @commands.command(name="id", pass_context=True)
    async def _do_id_all(self, ctx, *, query: str):
        """Monster info (main tab)"""
        await self._do_id(ctx, query)

    @commands.command(name="idna", pass_context=True)
    async def _do_id_na(self, ctx, *, query: str):
        """Monster info (limited to NA monsters ONLY)"""
        await self._do_id(ctx, query, na_only=True)

    async def _do_id(self, ctx, query: str, na_only=False):
        m, err, debug_info = self.findMonster(query, na_only=na_only)
        if m is not None:
            await self._do_idmenu(ctx, m, self.id_emoji)
        else:
            await self.bot.say(self.makeFailureMsg(err))

    @commands.command(name="id2", pass_context=True)
    async def _do_id2_all(self, ctx, *, query: str):
        """Monster info (main tab)"""
        await self._do_id2(ctx, query)

    @commands.command(name="id2na", pass_context=True)
    async def _do_id2_na(self, ctx, *, query: str):
        """Monster info (limited to NA monsters ONLY)"""
        await self._do_id2(ctx, query, na_only=True)

    async def _do_id2(self, ctx, query: str, na_only=False):
        m, err, debug_info = self.findMonster2(query, na_only=na_only)
        if m is not None:
            await self._do_idmenu(ctx, m, self.id_emoji)
        else:
            await self.bot.say(self.makeFailureMsg(err))

    @commands.command(name="evos", pass_context=True)
    async def evos(self, ctx, *, query: str):
        """Monster info (evolutions tab)"""
        m, err, debug_info = self.findMonster(query)
        if m is not None:
            await self._do_idmenu(ctx, m, self.evo_emoji)
        else:
            await self.bot.say(self.makeFailureMsg(err))

    @commands.command(name="mats", pass_context=True, aliases=['evomats', 'evomat'])
    async def evomats(self, ctx, *, query: str):
        """Monster info (evo materials tab)"""
        m, err, debug_info = self.findMonster(query)
        if m is not None:
            await self._do_idmenu(ctx, m, self.mats_emoji)
        else:
            await self.bot.say(self.makeFailureMsg(err))

    @commands.command(pass_context=True)
    async def pantheon(self, ctx, *, query: str):
        """Monster info (pantheon tab)"""
        m, err, debug_info = self.findMonster(query)
        if m is not None:
            menu = await self._do_idmenu(ctx, m, self.pantheon_emoji)
            if menu == EMBED_NOT_GENERATED:
                await self.bot.say(inline('Not a pantheon monster'))
        else:
            await self.bot.say(self.makeFailureMsg(err))

    @commands.command(pass_context=True)
    async def skillups(self, ctx, *, query: str):
        """Monster info (evolutions tab)"""
        m, err, debug_info = self.findMonster(query)
        if m is not None:
            menu = await self._do_idmenu(ctx, m, self.skillups_emoji)
            if menu == EMBED_NOT_GENERATED:
                await self.bot.say(inline('No skillups available'))
        else:
            await self.bot.say(self.makeFailureMsg(err))

    async def _do_idmenu(self, ctx, m, starting_menu_emoji):
        emoji_to_embed = self.get_id_emoji_options(m=m)
        return await self._do_menu(
            ctx,
            starting_menu_emoji,
            IdEmojiUpdater(emoji_to_embed, pad_info=self,
                           m=m, selected_emoji=starting_menu_emoji)
        )

    def get_id_emoji_options(self, m=None):
        id_embed = monsterToEmbed(m, self.get_emojis())
        evo_embed = monsterToEvoEmbed(m)
        mats_embed = monsterToEvoMatsEmbed(m)
        animated = m.has_animation
        pic_embed = monsterToPicEmbed(m, animated=animated)
        other_info_embed = monsterToOtherInfoEmbed(m)

        emoji_to_embed = OrderedDict()
        emoji_to_embed[self.id_emoji] = id_embed
        emoji_to_embed[self.evo_emoji] = evo_embed
        emoji_to_embed[self.mats_emoji] = mats_embed
        emoji_to_embed[self.pic_emoji] = pic_embed
        pantheon_embed = monsterToPantheonEmbed(m)
        if pantheon_embed:
            emoji_to_embed[self.pantheon_emoji] = pantheon_embed

        skillups_embed = monsterToSkillupsEmbed(m)
        if skillups_embed:
            emoji_to_embed[self.skillups_emoji] = skillups_embed

        emoji_to_embed[self.other_info_emoji] = other_info_embed

        # it's impossible for the previous/next ones to be accessed because
        # IdEmojiUpdater won't allow it, however they have to be defined
        # so that the buttons print in the first place

        emoji_to_embed[self.previous_monster_emoji] = None
        emoji_to_embed[self.next_monster_emoji] = None

        # remove emoji needs to be last
        emoji_to_embed[self.remove_emoji] = self.menu.reaction_delete_message
        return emoji_to_embed

    async def _do_evolistmenu(self, ctx, sm):
        monsters = sm.alt_evos
        monsters.sort(key=lambda m: m.monster_id)

        emoji_to_embed = OrderedDict()
        for idx, m in enumerate(monsters):
            emoji = char_to_emoji(str(idx))
            emoji_to_embed[emoji] = monsterToEmbed(m, self.get_emojis())
            if m.monster_id == sm.monster_id:
                starting_menu_emoji = emoji

        return await self._do_menu(ctx, starting_menu_emoji, EmojiUpdater(emoji_to_embed), timeout=60)

    async def _do_menu(self, ctx, starting_menu_emoji, emoji_to_embed, timeout=30):
        if starting_menu_emoji not in emoji_to_embed.emoji_dict:
            # Selected menu wasn't generated for this monster
            return EMBED_NOT_GENERATED

        emoji_to_embed.emoji_dict[self.remove_emoji] = self.menu.reaction_delete_message

        try:
            result_msg, result_embed = await self.menu.custom_menu(ctx, emoji_to_embed,
                starting_menu_emoji, timeout=timeout)
            if result_msg and result_embed:
                # Message is finished but not deleted, clear the footer
                result_embed.set_footer(text=discord.Embed.Empty)
                await self.bot.edit_message(result_msg, embed=result_embed)
        except Exception as ex:
            print('Menu failure', ex)

    @commands.command(pass_context=True, aliases=['img'])
    async def pic(self, ctx, *, query: str):
        """Monster info (full image tab)"""
        m, err, debug_info = self.findMonster(query)
        if m is not None:
            await self._do_idmenu(ctx, m, self.pic_emoji)
        else:
            await self.bot.say(self.makeFailureMsg(err))

    @commands.command(pass_context=True, aliases=['stats'])
    async def otherinfo(self, ctx, *, query: str):
        """Monster info (misc info tab)"""
        m, err, debug_info = self.findMonster(query)
        if m is not None:
            await self._do_idmenu(ctx, m, self.other_info_emoji)
        else:
            await self.bot.say(self.makeFailureMsg(err))

    @commands.command(pass_context=True)
    async def lookup(self, ctx, *, query: str):
        """Short info results for a monster query"""
        m, err, debug_info = self.findMonster(query)
        if m is not None:
            embed = monsterToHeaderEmbed(m)
            await self.bot.say(embed=embed)
        else:
            await self.bot.say(self.makeFailureMsg(err))

    @commands.command(pass_context=True)
    async def evolist(self, ctx, *, query):
        """Monster info (for all monsters in the evo tree)"""
        m, err, debug_info = self.findMonster(query)
        if m is not None:
            await self._do_evolistmenu(ctx, m)
        else:
            await self.bot.say(self.makeFailureMsg(err))

    @commands.command(pass_context=True, aliases=['leaders', 'leaderskills', 'ls'])
    async def leaderskill(self, ctx, left_query: str, right_query: str = None, *, bad=None):
        """Display the multiplier and leaderskills for two monsters

        If either your left or right query contains spaces, wrap in quotes.
        e.g.: ^leaderskill "r sonia" "b sonia"
        """
        if bad:
            await self.bot.say(inline('Too many inputs. Try wrapping your queries in quotes.'))
            return

        # Handle a very specific failure case, user typing something like "uuvo ragdra"
        if ' ' not in left_query and right_query is not None and ' ' not in right_query and bad is None:
            combined_query = left_query + ' ' + right_query
            nm, err, debug_info = self._findMonster(combined_query)
            if nm and left_query in nm.prefixes:
                left_query = combined_query
                right_query = None

        left_m, left_err, _ = self.findMonster(left_query)
        if right_query:
            right_m, right_err, _ = self.findMonster(right_query)
        else:
            right_m, right_err, = left_m, left_err

        err_msg = '{} query failed to match a monster: [ {} ]. If your query is multiple words, wrap it in quotes.'
        if left_err:
            await self.bot.say(inline(err_msg.format('Left', left_query)))
            return
        if right_err:
            await self.bot.say(inline(err_msg.format('Right', right_query)))
            return

        emoji_to_embed = OrderedDict()
        emoji_to_embed[self.ls_emoji] = monstersToLsEmbed(left_m, right_m)
        emoji_to_embed[self.left_emoji] = monsterToEmbed(left_m, self.get_emojis())
        emoji_to_embed[self.right_emoji] = monsterToEmbed(right_m, self.get_emojis())

        await self._do_menu(ctx, self.ls_emoji, EmojiUpdater(emoji_to_embed))

    @commands.command(name="helpid", pass_context=True, aliases=['helppic', 'helpimg'])
    async def _helpid(self, ctx):
        """Whispers you info on how to craft monster queries for ^id"""
        await self.bot.whisper(box(HELP_MSG))

    @commands.command(pass_context=True)
    async def padsay(self, ctx, server, *, query: str = None):
        """Speak the voice line of a monster into your current chat"""
        voice = ctx.message.author.voice
        channel = voice.voice_channel
        if channel is None:
            await self.bot.say(inline('You must be in a voice channel to use this command'))
            return

        speech_cog = self.bot.get_cog('Speech')
        if not speech_cog:
            await self.bot.say(inline('Speech seems to be offline'))
            return

        if server.lower() not in ['na', 'jp']:
            query = server + ' ' + (query or '')
            server = 'na'
        query = query.strip().lower()

        m, err, debug_info = self.findMonster(query)
        if m is not None:
            voice_id = m.voice_id_jp if server == 'jp' else m.voice_id_na
            base_dir = '/home/tactical0retreat/dadguide/data/media/voices'
            voice_file = os.path.join(base_dir, server, '{0:03d}.wav'.format(voice_id))
            header = '{} ({})'.format(monsterToHeader(m), server)
            if not os.path.exists(voice_file):
                await self.bot.say(inline('Could not find voice for ' + header))
                return
            await self.bot.say('Speaking for ' + header)
            await speech_cog.play_path(channel, voice_file)
        else:
            await self.bot.say(self.makeFailureMsg(err))

    @commands.group(pass_context=True)
    @checks.is_owner()
    async def padinfo(self, ctx):
        """PAD info management"""
        if ctx.invoked_subcommand is None:
            await send_cmd_help(ctx)

    @padinfo.command(pass_context=True)
    @checks.is_owner()
    async def setemojiservers(self, ctx, *, emoji_servers=''):
        """Set the emoji servers by ID (csv)"""
        self.settings.emojiServers().clear()
        if emoji_servers:
            self.settings.setEmojiServers(emoji_servers.split(','))
        await self.bot.say(inline('Set {} servers'.format(len(self.settings.emojiServers()))))

    def get_emojis(self):
        server_ids = self.settings.emojiServers()
        return [e for s in self.bot.servers if s.id in server_ids for e in s.emojis]

    def makeFailureMsg(self, err):
        msg = 'Lookup failed: {}.\n'.format(err)
        msg += 'Try one of <id>, <name>, [argbld]/[rgbld] <name>. Unexpected results? Use ^helpid for more info.'
        return box(msg)

    def findMonster(self, query, na_only=False):
        query = rmdiacritics(query)
        nm, err, debug_info = self._findMonster(query, na_only)

        monster_no = nm.monster_id if nm else -1
        self.historic_lookups[query] = monster_no
        dataIO.save_json(self.historic_lookups_file_path, self.historic_lookups)

        m = self.get_monster_by_no(nm.monster_id) if nm else None

        return m, err, debug_info

    def _findMonster(self, query, na_only=False):
        monster_index = self.index_na if na_only else self.index_all
        return monster_index.find_monster(query)

    def findMonster2(self, query, na_only=False):
        query = rmdiacritics(query)
        nm, err, debug_info = self._findMonster2(query, na_only)

        monster_no = nm.monster_id if nm else -1
        self.historic_lookups_id2[query] = monster_no
        dataIO.save_json(self.historic_lookups_file_path_id2, self.historic_lookups_id2)

        m = self.get_monster_by_no(nm.monster_id) if nm else None

        return m, err, debug_info

    def _findMonster2(self, query, na_only=False):
        monster_index = self.index_na if na_only else self.index_all
        return monster_index.find_monster2(query)


def setup(bot):
    print('padinfo bot setup')
    n = PadInfo(bot)
    bot.add_cog(n)
    bot.loop.create_task(n.reload_nicknames())
    print('done adding padinfo bot')


class PadInfoSettings(CogSettings):
    def make_default_settings(self):
        config = {
            'animation_dir': '',
        }
        return config

    def emojiServers(self):
        key = 'emoji_servers'
        if key not in self.bot_settings:
            self.bot_settings[key] = []
        return self.bot_settings[key]

    def setEmojiServers(self, emoji_servers):
        es = self.emojiServers()
        es.clear()
        es.extend(emoji_servers)
        self.save_settings()


def monsterToHeader(m: dadguide.DgMonster, link=False):
    msg = 'No. {} {}'.format(m.monster_no_na, m.name_na)
    return '[{}]({})'.format(msg, get_pdx_url(m)) if link else msg


def monsterToJpSuffix(m: dadguide.DgMonster):
    suffix = ""
    if m.roma_subname:
        suffix += ' [{}]'.format(m.roma_subname)
    if not m.on_na:
        suffix += ' (JP only)'
    return suffix


def monsterToLongHeader(m: dadguide.DgMonster, link=False):
    msg = monsterToHeader(m) + monsterToJpSuffix(m)
    return '[{}]({})'.format(msg, get_pdx_url(m)) if link else msg


def monsterToEvoText(m: dadguide.DgMonster):
    output = monsterToLongHeader(m)
    for ae in sorted(m.alt_evos, key=lambda x: int(x.monster_id)):
        output += "\n\t- {}".format(monsterToLongHeader(ae))
    return output


def monsterToThumbnailUrl(m: dadguide.DgMonster):
    return get_portrait_url(m)


def monsterToBaseEmbed(m: dadguide.DgMonster):
    header = monsterToLongHeader(m)
    embed = discord.Embed()
    embed.set_thumbnail(url=monsterToThumbnailUrl(m))
    embed.title = header
    embed.url = get_pdx_url(m)
    embed.set_footer(text='Requester may click the reactions below to switch tabs')
    return embed


def printEvoListFields(list_of_monsters, embed, name):
    if not len(list_of_monsters):
        return
    field_name = name.format(len(list_of_monsters))
    field_data = ''
    for ae in sorted(list_of_monsters, key=lambda x: int(x.monster_id)):
        field_data += "{}\n".format(monsterToLongHeader(ae, link=True))

    embed.add_field(name=field_name, value=field_data)


def monsterToEvoEmbed(m: dadguide.DgMonster):
    embed = monsterToBaseEmbed(m)

    if not len(m.alt_evos) and not m.evo_gem:
        embed.description = 'No alternate evos or evo gem'
        return embed

    printEvoListFields(m.alt_evos, embed, '{} alternate evo(s)')
    if not m.evo_gem:
        return embed
    printEvoListFields([m.evo_gem], embed, '{} evo gem(s)')

    return embed


def printMonsterEvoOfList(monster_list, embed, field_name):
    if not len(monster_list):
        return
    field_data = ''
    if len(monster_list) > 5:
        field_data = '{} monsters'.format(len(monster_list))
    else:
        item_count = min(len(monster_list), 5)
        for ae in sorted(monster_list, key=lambda x: x.monster_no_na, reverse=True)[:item_count]:
            field_data += "{}\n".format(monsterToLongHeader(ae, link=True))
    embed.add_field(name=field_name, value=field_data)


def monsterToEvoMatsEmbed(m: dadguide.DgMonster):
    embed = monsterToBaseEmbed(m)

    mats_for_evo = m.mats_for_evo

    field_name = 'Evo materials'
    field_data = ''
    if len(mats_for_evo) > 0:
        for ae in m.mats_for_evo:
            field_data += "{}\n".format(monsterToLongHeader(ae, link=True))
    else:
        field_data = 'None'
    embed.add_field(name=field_name, value=field_data)

    printMonsterEvoOfList(m.material_of, embed, 'Material for')
    if not m.evo_gem:
        return embed
    printMonsterEvoOfList(m.evo_gem.material_of, embed, "Tree's gem (may not be this evo) is mat for")
    return embed


def monsterToPantheonEmbed(m: dadguide.DgMonster):
    full_pantheon = m.series.monsters
    pantheon_list = list(filter(lambda x: x.evo_from is None, full_pantheon))
    if len(pantheon_list) == 0 or len(pantheon_list) > 6:
        return None

    embed = monsterToBaseEmbed(m)

    field_name = 'Pantheon: ' + m.series.name
    field_data = ''
    for monster in sorted(pantheon_list, key=lambda x: x.monster_no_na):
        field_data += '\n' + monsterToHeader(monster, link=True)
    embed.add_field(name=field_name, value=field_data)

    return embed


def monsterToSkillupsEmbed(m: dadguide.DgMonster):
    skillups_list = m.active_skill.skillups if m.active_skill else []
    if len(skillups_list) == 0:
        return None

    embed = monsterToBaseEmbed(m)

    field_name = 'Skillups'
    field_data = ''

    # Prevent huge skillup lists
    if len(skillups_list) > 8:
        field_data = '({} skillups omitted)'.format(len(skillups_list) - 8)
        skillups_list = skillups_list[0:8]

    for monster in sorted(skillups_list, key=lambda x: x.monster_no_na):
        field_data += '\n' + monsterToHeader(monster, link=True)

    if len(field_data.strip()):
        embed.add_field(name=field_name, value=field_data)

    return embed


def monsterToPicUrl(m: dadguide.DgMonster):
    return get_pic_url(m)


def monsterToPicEmbed(m: dadguide.DgMonster, animated=False):
    embed = monsterToBaseEmbed(m)
    url = monsterToPicUrl(m)
    embed.set_image(url=url)
    # Clear the thumbnail, don't need it on pic
    embed.set_thumbnail(url='')
    extra_links = []
    if animated:
        extra_links.append('Animation: {} -- {}'.format(monsterToVideoUrl(m), monsterToGifUrl(m)))
    if m.orb_skin_id is not None:
        extra_links.append('Orb Skin: {} -- {}'.format(monsterToOrbSkinUrl(m), monsterToOrbSkinCBUrl(m)))
    if len(extra_links) > 0:
        embed.add_field(name='Extra Links', value='\n'.join(extra_links))

    return embed


def monsterToVideoUrl(m: dadguide.DgMonster, link_text='(MP4)'):
    return '[{}]({})'.format(link_text, VIDEO_TEMPLATE.format(m.monster_no_jp))


def monsterToGifUrl(m: dadguide.DgMonster, link_text='(GIF)'):
    return '[{}]({})'.format(link_text, GIF_TEMPLATE.format(m.monster_no_jp))


def monsterToOrbSkinUrl(m: dadguide.DgMonster, link_text='Regular'):
    return '[{}]({})'.format(link_text, ORB_SKIN_TEMPLATE.format(m.orb_skin_id))


def monsterToOrbSkinCBUrl(m: dadguide.DgMonster, link_text='Color Blind'):
    return '[{}]({})'.format(link_text, ORB_SKIN_CB_TEMPLATE.format(m.orb_skin_id))


def monsterToGifEmbed(m: dadguide.DgMonster):
    embed = monsterToBaseEmbed(m)
    url = monsterToGifUrl(m)
    embed.set_image(url=url)
    # Clear the thumbnail, don't need it on pic
    embed.set_thumbnail(url='')
    return embed


def monstersToLsEmbed(left_m: dadguide.DgMonster, right_m: dadguide.DgMonster):
    lhp, latk, lrcv, lresist = left_m.leader_skill.data
    rhp, ratk, rrcv, rresist = right_m.leader_skill.data
    multiplier_text = createMultiplierText(lhp, latk, lrcv, lresist, rhp, ratk, rrcv, rresist)

    embed = discord.Embed()
    embed.title = 'Multiplier [{}]\n\n'.format(multiplier_text)
    description = ''
    description += '\n**{}**\n{}'.format(
        monsterToHeader(left_m, link=True),
        left_m.leader_skill.desc if left_m.leader_skill else 'None/Missing')
    description += '\n**{}**\n{}'.format(
        monsterToHeader(right_m, link=True),
        right_m.leader_skill.desc if right_m.leader_skill else 'None/Missing')
    embed.description = description

    return embed


def monsterToHeaderEmbed(m: dadguide.DgMonster):
    header = monsterToLongHeader(m, link=True)
    embed = discord.Embed()
    embed.description = header
    return embed


def monsterToTypeString(m: dadguide.DgMonster):
    return '/'.join([t.name for t in m.types])


def monsterToAcquireString(m: dadguide.DgMonster):
    acquire_text = None
    if m.farmable and not m.mp_evo:
        # Some MP shop monsters 'drop' in PADR
        acquire_text = 'Farmable'
    elif m.farmable_evo and not m.mp_evo:
        acquire_text = 'Farmable Evo'
    elif m.in_pem:
        acquire_text = 'In PEM'
    elif m.pem_evo:
        acquire_text = 'PEM Evo'
    elif m.in_rem:
        acquire_text = 'In REM'
    elif m.rem_evo:
        acquire_text = 'REM Evo'
    elif m.in_mpshop:
        acquire_text = 'MP Shop'
    elif m.mp_evo:
        acquire_text = 'MP Shop Evo'
    return acquire_text


def match_emoji(emoji_list, name):
    for e in emoji_list:
        if e.name == name:
            return e
    return name


def monsterToEmbed(m: dadguide.DgMonster, emoji_list):
    embed = monsterToBaseEmbed(m)

    info_row_1 = monsterToTypeString(m)
    acquire_text = monsterToAcquireString(m)

    info_row_2 = '**Rarity** {}\n**Cost** {}'.format(m.rarity, m.cost)
    if acquire_text:
        info_row_2 += '\n**{}**'.format(acquire_text)
    if m.is_inheritable:
        info_row_2 += '\n**Inheritable**'
    else:
        info_row_2 += '\n**Not inheritable**'

    embed.add_field(name=info_row_1, value=info_row_2)

    hp, atk, rcv, weighted = m.stats()
    if m.limit_mult > 0:
        lb_hp, lb_atk, lb_rcv, lb_weighted = m.stats(lv=110)
        stats_row_1 = 'Weighted {} | LB {} (+{}%)'.format(weighted, lb_weighted, m.limit_mult)
        stats_row_2 = '**HP** {} ({})\n**ATK** {} ({})\n**RCV** {} ({})'.format(
            hp, lb_hp, atk, lb_atk, rcv, lb_rcv)
    else:
        stats_row_1 = 'Weighted {}'.format(weighted)
        stats_row_2 = '**HP** {}\n**ATK** {}\n**RCV** {}'.format(hp, atk, rcv)
    embed.add_field(name=stats_row_1, value=stats_row_2)

    awakenings_row = ''
    for idx, a in enumerate(m.awakenings):
        as_id = a.awoken_skill_id
        as_name = a.name
        mapped_awakening = AWAKENING_MAP.get(as_id, as_name)
        mapped_awakening = match_emoji(emoji_list, mapped_awakening)

        # Wrap superawakenings to the next line
        if len(m.awakenings) - idx == m.superawakening_count:
            awakenings_row += '\n{}'.format(mapped_awakening)
        else:
            awakenings_row += ' {}'.format(mapped_awakening)

    awakenings_row = awakenings_row.strip()

    if not len(awakenings_row):
        awakenings_row = 'No Awakenings'

    killers_row = '**Available Killers:** {}'.format(' '.join(m.killers))

    embed.description = '{}\n{}'.format(awakenings_row, killers_row)

    active_header = 'Active Skill'
    active_body = 'None/Missing'
    active_skill = m.active_skill
    if active_skill:
        active_header = 'Active Skill ({} -> {})'.format(active_skill.turn_max,
                                                         active_skill.turn_min)
        active_body = active_skill.desc
    embed.add_field(name=active_header, value=active_body, inline=False)

    leader_skill = m.leader_skill
    ls_row = m.leader_skill.desc if leader_skill else 'None/Missing'
    ls_header = 'Leader Skill'
    if leader_skill:
        hp, atk, rcv, resist = m.leader_skill.data
        multiplier_text = createMultiplierText(hp, atk, rcv, resist)
        ls_header += " [ {} ]".format(multiplier_text)
    embed.add_field(name=ls_header, value=ls_row, inline=False)

    return embed


def monsterToOtherInfoEmbed(m: dadguide.DgMonster):
    embed = monsterToBaseEmbed(m)
    # Clear the thumbnail, takes up too much space
    embed.set_thumbnail(url='')

    body_text = '\n'
    stat_cols = ['', 'HP', 'ATK', 'RCV']
    for plus in (0, 297):
        body_text += '**Stats at +{}:**'.format(plus)
        tbl = prettytable.PrettyTable(stat_cols)
        tbl.hrules = prettytable.NONE
        tbl.vrules = prettytable.NONE
        tbl.align = "l"
        levels = (m.level, 110) if m.limit_mult > 0 else (m.level,)
        for lv in levels:
            for inh in (False, True):
                hp, atk, rcv, _ = m.stats(lv, plus=plus, inherit=inh)
                row_name = 'Lv{}'.format(lv)
                if inh:
                    row_name = '(Inh)'
                tbl.add_row([row_name.format(plus), hp, atk, rcv])
        body_text += box(tbl.get_string())

    search_text = YT_SEARCH_TEMPLATE.format(urllib.parse.quote(m.name_jp))
    skyozora_text = SKYOZORA_TEMPLATE.format(m.monster_no_jp)
    body_text += "\n**JP Name**: {} | [YouTube]({}) | [Skyozora]({})".format(
        m.name_jp, search_text, skyozora_text)

    if m.history_us:
        body_text += '\n**History:** {}'.format(m.history_us)

    body_text += '\n**Series:** {}'.format(m.series.name)
    body_text += '\n**Sell MP:** {:,}'.format(m.sell_mp)
    if m.buy_mp is not None:
        body_text += "  **Buy MP:** {:,}".format(m.buy_mp)

    if m.exp < 1000000:
        xp_text = '{:,}'.format(m.exp)
    else:
        xp_text = '{:.1f}'.format(m.exp / 1000000).rstrip('0').rstrip('.') + 'M'
    body_text += '\n**XP to Max:** {}'.format(xp_text)
    body_text += '  **Max Level:**: {}'.format(m.level)
    body_text += '\n**Rarity:** {} **Cost:** {}'.format(m.rarity, m.cost)

    # body_text += '\n**Google Translated:** {}'.format(tl_name)

    embed.description = body_text

    return embed


AWAKENING_MAP = {
    1: 'boost_hp',
    2: 'boost_atk',
    3: 'boost_rcv',
    4: 'reduce_fire',
    5: 'reduce_water',
    6: 'reduce_wood',
    7: 'reduce_light',
    8: 'reduce_dark',
    9: 'misc_autoheal',
    10: 'res_bind',
    11: 'res_blind',
    12: 'res_jammer',
    13: 'res_poison',
    14: 'oe_fire',
    15: 'oe_water',
    16: 'oe_wood',
    17: 'oe_light',
    18: 'oe_dark',
    19: 'misc_te',
    20: 'misc_bindclear',
    21: 'misc_sb',
    22: 'row_fire',
    23: 'row_water',
    24: 'row_wood',
    25: 'row_light',
    26: 'row_dark',
    27: 'misc_tpa',
    28: 'res_skillbind',
    29: 'oe_heart',
    30: 'misc_multiboost',
    31: 'killer_dragon',
    32: 'killer_god',
    33: 'killer_devil',
    34: 'killer_machine',
    35: 'killer_balance',
    36: 'killer_attacker',
    37: 'killer_physical',
    38: 'killer_healer',
    39: 'killer_evomat',
    40: 'killer_awoken',
    41: 'killer_enhancemat',
    42: 'killer_vendor',
    43: 'misc_comboboost',
    44: 'misc_guardbreak',
    45: 'misc_extraattack',
    46: 'teamboost_hp',
    47: 'teamboost_rcv',
    48: 'misc_voidshield',
    49: 'misc_assist',
    50: 'misc_super_extraattack',
    51: 'misc_skillcharge',
    52: 'res_bind_super',
    53: 'misc_te_super',
    54: 'res_cloud',
    55: 'res_seal',
    56: 'misc_sb_super',
    57: 'attack_boost_high',
    58: 'attack_boost_low',
    59: 'l_shield',
    60: 'l_attack',
    61: 'misc_super_comboboost',
    62: 'orb_combo',
    63: 'misc_voice',
    64: 'misc_dungeonbonus',
    65: 'reduce_hp',
    66: 'reduce_atk',
    67: 'reduce_rcv',
    68: 'res_blind_super',
    69: 'res_jammer_super',
    70: 'res_poison_super',
    71: 'misc_jammerboost',
    72: 'misc_poisonboost',
}


def createMultiplierText(hp1, atk1, rcv1, resist1, hp2=None, atk2=None, rcv2=None, resist2=None):
    if all([x is None for x in (hp2, atk2, rcv2, resist2)]):
        hp2, atk2, rcv2, resist2 = hp1, atk1, rcv1, resist1

    def fmtNum(val):
        return ('{:.2f}').format(val).strip('0').rstrip('.')

    text = "{}/{}/{}".format(fmtNum(hp1 * hp2), fmtNum(atk1 * atk2), fmtNum(rcv1 * rcv2))
    if resist1 > 0 or resist2 > 0:
        text += ' Resist {}%'.format(fmtNum(100 * (1 - (1 - resist1) * (1 - resist2))))
    return text
