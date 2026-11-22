# ARK Recruit Watch

A Discord bot for one ARK: Survival Ascended tribe. It posts one updating evaluation per recruit in a staff channel. Each card shows a 0–10 progress bar, the four point contributions, the last observed ARK status, the latest server switch and off-assignment joins. Staff make promotion decisions.

## Deploy to Railway

1. Put the contents of the project folder in your GitHub repository. The build root must contain Dockerfile and config.example.toml. Upload the entire release, including settings.py and setup_ui.py.
2. Connect the Railway service to that repository. Set **DISCORD_TOKEN** to your Discord bot token. Leave the custom Start Command empty so Railway uses the Dockerfile command. Attach a volume at **/app/data**, keep one replica, and deploy. The default SQLite path is /app/data/recruits.sqlite3 in this image.
3. In the Discord Developer Portal enable **Server Members Intent** and **Message Content Intent**. Install the bot with **bot** and **applications.commands** scopes into your tribe server. A fresh installation supports one tribe per database; finish initial setup with the bot installed in just that server.
4. Give the bot **View Channel** in tracked channels and **Read Message History** in the Overseer feeds so it can read a linked sample. Its private board needs **View Channel, Read Message History, Send Messages and Embed Links**. Hide the board from @everyone and recruits. Allow the bot and your officers.

## Configure the tribe in Discord

An officer with **Manage Server** runs **/recruit_setup**. The response is a private draft. Select a category in the menu, fill it in, then use **Save settings** to validate and apply the entire draft. Reopen the command any time. Panels expire after 15 minutes. If another officer saved first, reopen setup to get their changes.

| Choose in the setup menu | What to enter |
|---|---|
| Recruit role and Evaluation board | Pick the role that defines eligibility and the private text channel for cards. Officer reader roles can approve additional channel readers. Discord channel permissions still control access. |
| Overseer feeds | Pick all channels that publish join/leave posts. |
| Link Overseer post | Copy a real bot or webhook message link from a selected feed. Paste it into the form. For a dedicated single-server feed, enter its ARK server name. A blank server field clears that channel's mapping. The bot adds the sender to the draft and shows the matched event, if any. |
| Feed patterns | Enter how the event appears, e.g. `{player} joined {server}` and `{player} left {server}`. Use literal `\n` for embedded line breaks. For channel-mapped feeds you can omit {server}. Named regular expressions are also supported. Link a sample of each event type to check the result. |
| ARK servers | Enter your owned servers and the subset assigned to recruits, one exact server name per line. |
| Tracked text / voice channels | Pick channels for Discord activity. Each selection replaces that list; optional lists can be cleared. |
| Remaining categories | Edit score weights and targets, time windows, voice/session limits, reaction settings, cooldowns, board title, color, missions, reader roles and trusted senders. |

Saving applies settings to the running bot. Scorecards update on the refresh cycle. Changing the recruit role makes members without the new role ineligible and their records are removed on reconciliation. Reducing retention removes older evidence on the next refresh.

**Upgrading an existing deployment.** Keep the same SQLite volume and RECRUIT_DB if you set one. The first start imports your existing config.toml or RECRUIT_CONFIG_TOML into SQLite when available. Remove the old custom Start Command. Keep the old configuration variable through the first successful start if you want its settings imported. Subsequent starts load the saved Discord configuration. If only an older database is available, it remembers its guild and role and waits for setup before reconciliation. Back up the database before upgrading.

**Other hosts.** Set DISCORD_TOKEN and run `docker compose up --build -d`, or use Python 3.11+, install requirements.txt and run `python bot.py`. The Discord setup is the same.

### Officer workflow

1. Run `/recruit_status` to check board access and each feed's latest parser match. The bot checks the effective channel permissions of current members each refresh. If @everyone, the recruit role, any current recruit or any other unapproved member can read the channel, publication pauses and it attempts to remove existing cards. Discord permissions take effect immediately, so keep the channel private before configuring it. Administrators can see private channels. A recruit who also has Administrator access prevents the board from publishing.
2. Use `/recruit_link` to verify identity, then `/recruit_compare` for the rolling-window summary and the latest seven active UTC dates. Staff commands reply privately to the caller. The bot sends no recruit DMs or score announcements. Disclosure of tribe activity logging can live in the server rules while individual scores remain staff-only.
3. Draw a task idea with `/recruit_mission`. The officer hands out the assignment and verifies completion. Record it with `/recruit_credit` or correct an erroneous entry using `/recruit_uncredit`. The mission deck is an editable list.
4. Use `/recruit_review` to see the evidence behind a score. Manage Server is required to run commands. board_reader_roles grants permission for designated roles to view cards if their Discord channel permissions already allow access.

### How to read the activity comparison

**ARK time** counts paired join/leave observations on the same server. A newer join supersedes an unfinished one. An unmatched leave never closes a session on another server. Open sessions, missing endpoints and pairs longer than max_session_hours produce no duration. Time is clipped at the window boundary and split at UTC midnight. Only retained evidence is available. A session can include AFK time.

**Text participation.** Messages and reactions have separate counters and cooldowns. A message sent during a paired ARK session also appears in the message-overlap count.

**Emoji reactions.** A recruit can earn one credit for reacting to another current human member's message in a selected text channel. Standard and custom emoji qualify. The bot records the first qualifying addition per recruit and target message while the event is retained. Removing it, re-adding it, or choosing a different emoji on that message earns no additional credit. The cooldown applies across all selected channels. The bot skips events whose author it cannot verify. Reaction records contain the target message ID and time. They contain no message text or emoji history.

| config.toml setting | Default | Effect |
|---|---:|---|
| `track_reactions` | `true` | Collect new reaction credits in `chat_channels`. |
| `reaction_activity_days` | `true` | A credited reaction qualifies a Discord day. A message and a reaction on the same UTC date still earn one day. Set to `false` for reporting only. |
| `reaction_cooldown_minutes` | `10` | Minutes between reaction credits for the same recruit. Independent of the message cooldown. |

Save the Discord setup draft after changing settings. Turning collection off stops new credits. Turning off reaction activity days also removes previously credited reactions from scoring. The board and `/recruit_compare` show the retained reaction count in the review window.

**Voice.** Set `voice_channels` to the voice or stage channel IDs to observe, or select them in the wizard. Give the bot View Channel there. The comparison shows total, solo, muted and deafened time. Solo means exactly one human is present. Muted and deafened include self- and server-applied states. The counters can overlap. Stage audience membership alone does not count as muted. Voice statistics do not affect the score. Observations are checkpointed each minute. Downtime and the uncheckpointed tail of an abrupt stop are excluded. The bot never captures audio.

**Multiple Overseer feeds:** put each channel ID in feed_channels and allow the actual sender IDs. Use one consistent join/leave parser across the selected channels. If a feed omits server names, map its channel ID to the exact server label in channel_servers. /recruit_compare displays up to five servers by paired time. The board compares successive observed destinations across channels. Configure one authoritative feed per event stream to avoid mirrored posts. Other Discord servers, DMs, presence duration and unobserved ARK destinations are outside this bot's visibility.

## Optional offline feed checks

The bundled patterns are examples. The vendor describes a join/leave feed but your actual channel layout must be checked. Use a new-message log, not a continuously edited player-list panel. No vendor API key is needed.

Copy one real join and one real leave into separate temporary text files. For embeds, put author name, title, description, each field as `Name: value`, then footer on separate lines, skipping empty items. Each embed and the plain message body are parsed separately. A pattern should identify a single event, capture the unique player under `(?P<player>...)` and the destination or departure server under `(?P<server>...)`. Use a channel_servers mapping for a dedicated single-server feed without server labels. Parser inputs use Discord post time. Delayed deliveries can therefore shift observed times.

For the shipped example, a file containing `Player: EOS-A joined The Island 101` produces `join | eos-a | The Island 101`. For an optional local test, run configure.py or provide a local config.toml, then check both files:
```sh
python check_feed.py join.txt --channel 100000000000000005
python check_feed.py leave.txt --channel 100000000000000005
```

After launch, use `/recruit_feed` to check the last accepted feed post, matched and unmatched post counts, and saved recruit events. A matched post with no saved event can belong to an unlinked player or someone without the recruit role. The diagnostics reset on restart.

## Evaluation defaults

| Factor | Full credit | Points |
|---|---|---:|
| Recruit tenure | 14 days since the role was first observed | 2 |
| Assigned-server activity | Sightings on 6 distinct UTC dates | 3 |
| Discord participation | Messages or qualifying reactions on 5 distinct UTC dates in selected channels | 1 |
| Effective contributions | 4 distinct staff-verified tasks | 4 |

Each factor earns its weight multiplied by the fraction of its target reached, capped at full credit. For example, seven days of tenure, three assigned-server days, five Discord days and two contributions total **5.5/10**. Activity and contributions use a rolling 14-day window. Tenure continues from enrollment. Edit weights, targets, window, message cooldown, retention, status expiry and refresh cadence in /recruit_setup, then save. Weights must total ten. Use zero weight to disable a factor.

Use `/recruit_credit member task` for verified work such as a completed resource run or a defended base shift. Include a distinct task reference. Repeating the same reference for the same member is ignored while the record is retained. Use `/recruit_review member` for the current evaluation and up to five recent sightings and contribution records.

## Reading a card

A switch compares consecutive observed joins. An off-assignment join is labelled either **owned, outside assignment** or **outside configured ownership**. The latter can also mean your ownership list needs updating. Leaving a server alone provides no destination. A stale join remains a timestamped sighting, and an open observed session expires from the online display after the configured interval. A visit elsewhere earns no assigned-server points. It carries no automatic penalty.

Only current recruits generate individual records. Discord participation stores credited message IDs and timestamps, without chat text. Staff credit records store the task reference and staff ID. Removing the role or leaving deletes the recruit record, identity link and evidence. The next successful board refresh removes their card. When the bot starts it reconciles current roles. Role changes and events occurring entirely during downtime cannot be reconstructed. Existing recruits start at first observation. Historical activity is never backfilled.

The default database is data/recruits.sqlite3. RECRUIT_DB overrides its path. RECRUIT_CONFIG can identify a legacy file for first-start import. Use a new database for a different guild or recruit role. The activity retention default is 60 days. Configuration changes to server lists or weights recalculate scores from retained observations.

## Verify locally

Run `python -m unittest -v` for the offline scoring, role scope, role removal, duplicate, cooldown, parser, switch and stale-status checks. For a live smoke test, use a test recruit, link the player, observe a genuine join, issue a contribution, then remove the role and confirm the evaluation disappears after the refresh interval.

Documentation: [Overseer EOS](https://asaservices.dev/overseer/) · [Discord gateway intents](https://discordpy.readthedocs.io/en/stable/intents.html)
