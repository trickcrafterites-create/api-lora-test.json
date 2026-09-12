# Character catalog provenance

The catalog contains **102 newly selected Civitai character LoRAs**, plus the endpoint's existing **Kim Possible**, for **103 files**. New files total **15,201,215,280 bytes**; including Kim, **15,429,671,796 bytes** (about 14.37 GiB). Base checkpoint storage is additional.

## Source and selection

The requested directory is [Civitai.red character models](https://civitai.red/models?tag=character). Its REST endpoint returned HTTP 403 in this environment. Metadata and version-specific download links were collected from the canonical [Civitai REST API](https://civitai.com/api/v1/models), whose reference is linked from the [official API wiki](https://github.com/civitai/civitai/wiki/REST-API-Reference).

Collection on 2026-09-12 inspected eight 100-model pages using `types=LORA`, `tag=character`, `baseModels=Illustrious`, `nsfw=false`, `sort=Most Downloaded`, and `period=AllTime`. Metadata alone did not prove anonymous download access: 361 candidates had suitable public primary SafeTensor files below 350 MiB and a `Rent` permission; 114 returned valid anonymous ranged responses before rate limiting. Curation retained 102 distinct named fictional characters, including fictional VTuber avatars, and excluded generic sliders, styles, mixed character packs, duplicate character identities, and duplicate outfit/transform variations. No source image galleries were collected.

Every new entry has **exactly `baseModel: Illustrious`**, a pinned model/version/file ID, source SHA256, exact byte count from `Content-Range`, creator attribution, and author permission flags. This is architecture compatibility; image quality with the endpoint's checkpoint has not been GPU-tested.

## What was verified

Each new download returned HTTP 206 without cookies, tokens, or login. Only bytes 0-7 were read; the value is a plausible SafeTensors JSON-header length, and the full size was obtained from `Content-Range`. Complete LoRA weights were not downloaded during collection. SHA256 values come from Civitai file metadata, and the installer verifies the full downloaded files against those hashes. An eight-byte check cannot establish complete-file integrity by itself.

The existing Kim Possible file was separately downloaded in full: **228,456,516 bytes**, SHA256 `642f2ec5f68fa9451fb4359aaf8546ee6c27aba7dfdd8c8107a3854de9f5be91`. Its original filename is retained. Its SafeTensors metadata identifies the activation token `kimp`; its creator/permission information was not provided in the existing Dockerfile and remains marked unknown.

All 102 new source models include `Rent` in `allowCommercialUse`, the Civitai flag for third-party generation-service use. Other author flags are preserved verbatim per entry; permission for service use does not automatically imply permission to redistribute the weight files. Attribution is provided below even when `allowNoCredit` is true. No license assertion is made for the retained legacy Kim file.

## Activation and strength

`triggerWords` contains concise identity/default-outfit tokens selected from the published trained words. Evernight's tokens are published in its version description. Full mutually exclusive outfit prompts are intentionally not appended together. Empty arrays mean no concise activation token was published; those entries are Aisha Udgard, Tsuna Nekota, and Marinette Dupain-Cheng. See each source page for detailed appearance/outfit instructions. Most strengths are starting values of 0.8, not quality guarantees; values found in source examples were used for Momo, Tsunade, Hinata, Zelda, Mitsuri, and Marinette.

## Reproducing collection

`character-seeds.json` records the exact manually selected model/version/file IDs, display names, franchises, and activation tokens. `character-loras.json` is the build/UI contract.

```sh
# Offline schema, uniqueness, hash, size, permission and count validation
python scripts/collect_loras.py validate

# Recheck the exact 102 pinned source versions and atomically refresh metadata
# Existing Kim is preserved. Any unavailable model, changed hash or rate limit aborts.
python scripts/collect_loras.py refresh --delay 2

# Optional discovery; output is candidates only and must be manually curated
python scripts/collect_loras.py discover --pages 8 --output work/candidates.json
```

Refresh preserves hashes for existing version IDs and never writes a partial result. Download links can later require authentication, disappear, or be rate limited; the installer should fail visibly rather than silently ship a smaller catalog.

## Author attribution

| Character | Creator | Pinned source version |
| --- | --- | --- |
| 2B | NeogenArt | [1835277](https://civitai.red/models/1429821?modelVersionId=1835277) |
| Ada Wong | richyrich515 | [1681083](https://civitai.red/models/1486174?modelVersionId=1681083) |
| Ai Hoshino | duongve13112002 | [1096827](https://civitai.red/models/955927?modelVersionId=1096827) |
| Aisha Udgard | AnimeGuy01 | [2383812](https://civitai.red/models/2106920?modelVersionId=2383812) |
| Akane Kurokawa | duongve13112002 | [2810868](https://civitai.red/models/962780?modelVersionId=2810868) |
| Alina Clover | Ibukimakisiko | [1624335](https://civitai.red/models/1128462?modelVersionId=1624335) |
| Alma | news_v_com | [1464422](https://civitai.red/models/1297524?modelVersionId=1464422) |
| Android 18 | HellKaiza | [1700380](https://civitai.red/models/972465?modelVersionId=1700380) |
| Anna Yanami | Ibukimakisiko | [1565606](https://civitai.red/models/859113?modelVersionId=1565606) |
| Anya Forger | duongve13112002 | [2542764](https://civitai.red/models/1027497?modelVersionId=2542764) |
| Aris Tendou | Ibukimakisiko | [1682558](https://civitai.red/models/1462359?modelVersionId=1682558) |
| Arisu Tachibana | duongve13112002 | [1059964](https://civitai.red/models/946720?modelVersionId=1059964) |
| Becky Blackbell | duongve13112002 | [2542774](https://civitai.red/models/1047702?modelVersionId=2542774) |
| Bready | richyrich515 | [1577115](https://civitai.red/models/1395245?modelVersionId=1577115) |
| Bulma | HellKaiza | [1484428](https://civitai.red/models/1292157?modelVersionId=1484428) |
| Celia Claire | Ibukimakisiko | [1239263](https://civitai.red/models/866095?modelVersionId=1239263) |
| Chen Qianyu | Reijiboo | [2620727](https://civitai.red/models/2329750?modelVersionId=2620727) |
| Chi-Chi | HellKaiza | [1730584](https://civitai.red/models/958026?modelVersionId=1730584) |
| Chinatsu Kano | duongve13112002 | [1203970](https://civitai.red/models/1058718?modelVersionId=1203970) |
| Chloe von Einzbern | C2P | [1995009](https://civitai.red/models/1762845?modelVersionId=1995009) |
| Cinderella | richyrich515 | [1143344](https://civitai.red/models/1019635?modelVersionId=1143344) |
| Crown | richyrich515 | [1314089](https://civitai.red/models/1168049?modelVersionId=1314089) |
| Cure Answer | oyorun0707 | [2649912](https://civitai.red/models/2298875?modelVersionId=2649912) |
| Cure Arcana Shadow | oyorun0707 | [2851375](https://civitai.red/models/2299086?modelVersionId=2851375) |
| Cure Eclair | oyorun0707 | [3166595](https://civitai.red/models/2299185?modelVersionId=3166595) |
| Cure Idol | oyorun0707 | [2244609](https://civitai.red/models/1199170?modelVersionId=2244609) |
| Cure Wink | oyorun0707 | [2244625](https://civitai.red/models/1216915?modelVersionId=2244625) |
| Dorothy | richyrich515 | [1659482](https://civitai.red/models/1467265?modelVersionId=1659482) |
| Ema Aizawa | andraste | [2439947](https://civitai.red/models/1327407?modelVersionId=2439947) |
| Emma Frost | guy90 | [1642054](https://civitai.red/models/1431314?modelVersionId=1642054) |
| Evelyn Chevalier | news_v_com | [1327752](https://civitai.red/models/1179892?modelVersionId=1327752) |
| Evernight / March 7th | C2P | [2135711](https://civitai.red/models/1140194?modelVersionId=2135711) |
| Fiona Frost | duongve13112002 | [2542784](https://civitai.red/models/1049397?modelVersionId=2542784) |
| Firefly | C2P | [1291255](https://civitai.red/models/1148092?modelVersionId=1291255) |
| Flandre Scarlet | RDMPrompt | [3154896](https://civitai.red/models/1127364?modelVersionId=3154896) |
| Gardevoir | Midnightcrawler | [1190237](https://civitai.red/models/468686?modelVersionId=1190237) |
| Gwen Tennyson | The_LeafMakerGod | [1181189](https://civitai.red/models/1052666?modelVersionId=1181189) |
| Hana Uzaki | dpsjksjx614 | [1061435](https://civitai.red/models/917380?modelVersionId=1061435) |
| Hayase Nagatoro | nrocka | [1213588](https://civitai.red/models/405278?modelVersionId=1213588) |
| Helen Parr / Elastigirl | Ty_Lee | [1352133](https://civitai.red/models/1200810?modelVersionId=1352133) |
| Helm | richyrich515 | [1369180](https://civitai.red/models/1215490?modelVersionId=1369180) |
| Hermione Granger | plumbu82286 | [2472457](https://civitai.red/models/1282238?modelVersionId=2472457) |
| Hikari Tachibana | Ibukimakisiko | [1020687](https://civitai.red/models/912054?modelVersionId=1020687) |
| Hinano Tachibana | andraste | [1789316](https://civitai.red/models/1580254?modelVersionId=1789316) |
| Hinata Hyuga | turkey910 | [1244155](https://civitai.red/models/91861?modelVersionId=1244155) |
| Hiro Shinosawa | Ibukimakisiko | [1138174](https://civitai.red/models/1015186?modelVersionId=1138174) |
| Ibuki Tanga | Ibukimakisiko | [1188303](https://civitai.red/models/1058938?modelVersionId=1188303) |
| Illyasviel von Einzbern | C2P | [1932645](https://civitai.red/models/1707826?modelVersionId=1932645) |
| Inori Yuitsuka | Ibukimakisiko | [1536845](https://civitai.red/models/1128520?modelVersionId=1536845) |
| Kana Arima | duongve13112002 | [2810857](https://civitai.red/models/1042202?modelVersionId=2810857) |
| Kaoru Kamiya | AnimeGuy01 | [2269501](https://civitai.red/models/2004809?modelVersionId=2269501) |
| Keqing | richyrich515 | [1189874](https://civitai.red/models/1060298?modelVersionId=1189874) |
| Kim Possible | Unknown (retained from existing Dockerfile) | [existing file](https://www.dropbox.com/scl/fi/kosfszac2jmlq1sa25pgv/KimPossibleIllustrious2.0JLFO.safetensors?rlkey=h7s3ndipjhm9eu78fyxprtzqr&dl=1) |
| Kisaki Ryuuge | Ibukimakisiko | [1195575](https://civitai.red/models/1065259?modelVersionId=1195575) |
| Koharu Shimoe | Ibukimakisiko | [1682497](https://civitai.red/models/1487403?modelVersionId=1682497) |
| Kotone Fujita | Ibukimakisiko | [1187325](https://civitai.red/models/1058083?modelVersionId=1187325) |
| Kyouka | Elesico | [2871476](https://civitai.red/models/1464135?modelVersionId=2871476) |
| Link | Legendaer | [2047811](https://civitai.red/models/621508?modelVersionId=2047811) |
| Mahiro Oyama | Ibukimakisiko | [1163164](https://civitai.red/models/1036986?modelVersionId=1163164) |
| Maiden | richyrich515 | [1143339](https://civitai.red/models/1019633?modelVersionId=1143339) |
| Marciana | richyrich515 | [1195902](https://civitai.red/models/1065535?modelVersionId=1195902) |
| Marie Rose | Primarch | [2301375](https://civitai.red/models/779892?modelVersionId=2301375) |
| Marinette Dupain-Cheng / Ladybug | Tetete80099 | [3128825](https://civitai.red/models/684770?modelVersionId=3128825) |
| Mellow | affa1988 | [3224110](https://civitai.red/models/2629210?modelVersionId=3224110) |
| Mikasa Ackerman | andinmaro146 | [1210143](https://civitai.red/models/442186?modelVersionId=1210143) |
| Mira | kedicpeep | [1929015](https://civitai.red/models/1704570?modelVersionId=1929015) |
| Misora | Elesico | [2514139](https://civitai.red/models/1631650?modelVersionId=2514139) |
| Mita | xbamaris | [1237445](https://civitai.red/models/1060071?modelVersionId=1237445) |
| Mitsuri Kanroji | turkey910 | [1261939](https://civitai.red/models/91840?modelVersionId=1261939) |
| Miyu Edelfelt | C2P | [1991781](https://civitai.red/models/1759947?modelVersionId=1991781) |
| Mizumiya Su | BenkyouDekinAI | [2624230](https://civitai.red/models/933301?modelVersionId=2624230) |
| Modernia | richyrich515 | [1513668](https://civitai.red/models/1340394?modelVersionId=1513668) |
| Momo Ayase | turkey910 | [1064605](https://civitai.red/models/942367?modelVersionId=1064605) |
| Mythra | richyrich515 | [1140381](https://civitai.red/models/1017067?modelVersionId=1140381) |
| Nezuko Kamado | QuestGlitch | [1412426](https://civitai.red/models/1252857?modelVersionId=1412426) |
| Orihime Inoue | richyrich515 | [1301963](https://civitai.red/models/1157558?modelVersionId=1301963) |
| Pecorine | Elesico | [2753074](https://civitai.red/models/949620?modelVersionId=2753074) |
| Perlica | Reijiboo | [2620310](https://civitai.red/models/2329377?modelVersionId=2620310) |
| Princess Zelda | turkey910 | [1393168](https://civitai.red/models/91825?modelVersionId=1393168) |
| Privaty | richyrich515 | [1436697](https://civitai.red/models/1273499?modelVersionId=1436697) |
| Pyra | richyrich515 | [1140380](https://civitai.red/models/1017066?modelVersionId=1140380) |
| Rapunzel | richyrich515 | [1577428](https://civitai.red/models/1395548?modelVersionId=1577428) |
| Reisalin Stout / Ryza | tappy | [1357021](https://civitai.red/models/373967?modelVersionId=1357021) |
| Revy | MetalchromeX | [1639932](https://civitai.red/models/1450442?modelVersionId=1639932) |
| Rimuru Tempest | andinmaro146 | [1189464](https://civitai.red/models/392486?modelVersionId=1189464) |
| Roxy Migurdia | Ibukimakisiko | [1299675](https://civitai.red/models/1155590?modelVersionId=1299675) |
| Ruby Hoshino | duongve13112002 | [2810860](https://civitai.red/models/955545?modelVersionId=2810860) |
| Rumi | kedicpeep | [1828148](https://civitai.red/models/1615349?modelVersionId=1828148) |
| Runa Shinomiya | andraste | [2540886](https://civitai.red/models/1342767?modelVersionId=2540886) |
| Sakuya Izayoi | RDMPrompt | [2401473](https://civitai.red/models/1133848?modelVersionId=2401473) |
| Satori Komeiji | RDMPrompt | [2377116](https://civitai.red/models/1171460?modelVersionId=2377116) |
| Scarlet | richyrich515 | [1600882](https://civitai.red/models/1416345?modelVersionId=1600882) |
| Seiko Ayase | WhiteZ | [1914522](https://civitai.red/models/831426?modelVersionId=1914522) |
| Shigure Ui | Ibukimakisiko | [1215571](https://civitai.red/models/1082590?modelVersionId=1215571) |
| Shirakami Fubuki | holostrawberry | [1385098](https://civitai.red/models/1204238?modelVersionId=1385098) |
| Sora | richyrich515 | [1968062](https://civitai.red/models/1739002?modelVersionId=1968062) |
| Tsuna Nekota | andraste | [2972989](https://civitai.red/models/1218512?modelVersionId=2972989) |
| Tsunade | turkey910 | [1075687](https://civitai.red/models/412136?modelVersionId=1075687) |
| Videl | HellKaiza | [1218167](https://civitai.red/models/940615?modelVersionId=1218167) |
| Yor Forger | duongve13112002 | [2542753](https://civitai.red/models/1022054?modelVersionId=2542753) |
| Yukari Yakumo | RDMPrompt | [2985562](https://civitai.red/models/1189744?modelVersionId=2985562) |
| Yuni | Ibukimakisiko | [1420270](https://civitai.red/models/1259576?modelVersionId=1420270) |
| Zoey | kedicpeep | [1929026](https://civitai.red/models/1704581?modelVersionId=1929026) |
