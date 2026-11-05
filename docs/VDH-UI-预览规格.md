# VDH 交互/显示/预览 规格（复刻依据）

> 调查方法：REA 6.8.0 静态分析（`analyze-javascript-application`，证据 `ev_7e975070a2b5dcd28dbb6e37cef0d8070989a524df130337504d5bbab4a1fe5f`、`D:\rea\.reatmp\vdh-ui.json`）+ 对 `content/panel.js`、`content/register_components.js`、`content/sidebar.html` 的定点扫描。
> 目标扩展：`jmkaglaafmhbcpleggkmaliipiilhldn` 10.6.7.2（Edge 商店版 VDH）。
> 本文件只描述"看到什么"，不含推测；推测另标 ⚠️。

## 1. 卡片结构（media-template）

```
hbox#hbox_top
├─ stack#stack_top
│  ├─ com-media-preview        # 预览层：封面 <video poster> / 内联播放
│  ├─ div#shadow               # 底部渐变遮罩
│  └─ vbox#vbox_tags
│     └─ com-media-meta        # 徽标行
├─ vbox#vbox_right
│  ├─ com-media-discovered     # 状态：已发现
│  ├─ com-media-downloading    # 状态：下载中
│  └─ com-media-downloaded     # 状态：已下载
└─ vbox#vbox_dismiss
   └─ com-dismiss-button       # 移除（×）
```

三种状态组件互斥显示，切换靠 `isDiscovered()/isDownloading()/isDownloaded()`。

## 2. 预览层（com-media-preview）

```html
<video autoplay disablepictureinpicture disableremoteplayback
       loop="true" muted="true" playsinline="true">
```

- 封面：`poster = media.thumbnail_url.or(meta.thumbnail_url).map(o=>o.href).unwrapOr("/bitmaps/empty-thumbnail.png")`。
- 自动预览：仅 popup（工具栏下拉）模式，对第一张卡调一次 `activatePreview()`；侧栏模式不自动播。
- `activate()`：`preview_mode != "video"` 直接返回；无 `preview_url` → 元素加 `preview_incoming` 类（转圈态）+ 发消息 `{name:"request_preview", data:{tab_id, media_hash}}`；已有 `preview_url` → `video.src = preview_url` 并静音自动播放，失败 `console.error("Invalid preview video")` 并清空 src。
- `deactivate()`：暂停播放。
- `on_preview_available{tab_id,media_hash,filename}`：校验 tab/hash 匹配 → 从 OPFS 取文件 → `URL.createObjectURL(file)` → 设 `src`。
- `on_no_preview{media_hash}`：移除 `preview_incoming`，回到封面。
- 设置 `preview_mode`：`none | video | image`，默认 **video**。

### 预览素材怎么来的（服务端 + worker）

1. 服务端收到 `request_preview`：查该 tab 的媒体表；媒体不存在 → `on_no_preview`；`type=="http_playlist" && !supports_byte_ranges` → `on_no_preview`。
2. 按 `` `${tab_id}_${media_hash}` `` 查缓存，命中直接回 `on_preview_available`。
3. 远程 behaviour `DISABLE_PREVIEW_LOADING` 命中该主机 → 不预览。
4. 否则按媒体类型选 strategy：`m3u8_video_preview` / `youtube_video_preview` / `mpd_video_preview` / `http_video_preview_jsfetch`，走一次"微型下载"。
5. worker（`m3u8_video_preview`）：取播放列表，`s = is_live ? 最后一条 : floor(条数/2)`，只解码这一条视频分片，remux 成 mp4（`avoid_negative_ts=make_zero`，只写视频轨，有音轨时附带）。**即"抓中间一条分片"（约 2–6 秒），不是"前 N 秒"。**
6. 成功 → `internal_filename` 入缓存 → `on_preview_available`。

## 3. 徽标行（com-media-meta）

`hbox#hbox_meta` 依次：

| 元素 | 含义 |
|---|---|
| `div#div_favicon` | 站点 favicon |
| `p#p_star` | 星标（收藏/已保存标记） |
| `p#p_duration` | 视频时长 |
| `p#p_size` | 文件大小 |
| `p#p_subtitles` | 有字幕时的字幕图标 |

## 4. 媒体标签（com-media-tags）

`tag_hls`、`tag_m3u8`、`tag_http`、`tag_yt`（文案 "fmt"）、`tag_mpd`、`tag_free`（文案 "FREE"），按需显示。

## 5. 已发现状态（com-media-discovered）

```
p.media_filename
├─ com-media-tags
├─ span#span_directory      # 保存目录（可点击）
├─ span#span_filename       # contenteditable，可改名
├─ "." + span#span_extension
└─ button（重命名）
hbox.media_hbox (pack=end)
├─ button（重命名）
├─ com-media-selector       # 格式选择器
├─ div#box_drm              # ⚠️ DRM 标记 + tooltip
└─ hbox#box_combo
   ├─ button#action_download        # 下载
   ├─ button#action_download_as     # 下载为（带菜单）
   ├─ button#action_download_audio  # 仅音频
   └─ button#action_copy            # 复制链接
   + com-native-menu#menu_options
```

## 6. 格式选择器（com-media-selector）

```html
<com-native-menu id="menu_options" use-chevron>
  <hbox slot="face" id="face">
    <span id="span_demuxer"></span>
    <span id="span_size"></span>
  </hbox>
</com-native-menu>
```

- face 显示：`demuxer`（大写，如 `MP4`）+ `height + "p"`（如 `1080p`）。
- `quality` 属性分级：`<1080` → "meh"，`==1080` → "good"，`>1080` → "very_good"（CSS 上色）。
- 无分辨率有码率时，face 显示码率。
- 菜单项文案：`[DEMUXER, "1920x1080", "5000K/s"].join(" - ")`。

## 7. 下载中状态（com-media-downloading）

```
p.media_filename（tags + 文件名）
hbox#hbox_progressbar_container
├─ div#div_progressbar     # translateX(-(100-percent)%) 驱动进度
├─ span#span_live_icon     # 直播标记
├─ span#span_percent       # "42%"
├─ spacer
└─ span#span_bitrate       # "3.2M/s"
hbox#box_combo
└─ button#button_stop      # 停止
```

## 8. 已下载状态（com-media-downloaded）

`p#p_filename`、`com-media-button-origin`（在来源页播放）、`button#button_retry`、`button#button_rm`、`button#button_not_playing`。

## 9. 默认设置（panel.js 持久化默认值）

```json
{
  "ui_theme": "system",
  "use_context_menu": true,
  "preferred_quality": 1080,
  "preferred_av_muxer": "mp4",
  "preview_mode": "video",
  "popup_size": "medium",
  "font_size": "default",
  "preferred_discovered_media_order": "SMART",
  "max_concurrent_downloads": 6,
  "show_desktop_notifications": true
}
```

## 10. 复刻到本项目（Qt）的映射

| VDH | 本项目落点 |
|---|---|
| com-media-preview（封面 + 内联 video） | `MediaCard` 左侧 `PreviewWidget`：QLabel 封面 → 点击/自动触发 `vdh_capture.preview` 生成短片 → `QMediaPlayer` + `QVideoWidget` 内联循环静音播放 |
| 预览素材生成（中间分片 remux） | `vdh_capture/preview.py`：ffmpeg `-ss 中间点 -t 6 -c copy` 转 mp4，按 URL+起点缓存 |
| com-media-meta | 卡片徽标行：favicon、时长、大小、清晰度、字幕 |
| com-media-tags | `HLS`/`M3U8`/`MPD`/`MP4`/`FREE` 徽标 |
| com-media-selector | 现有 `AnimatedComboBox`：face "MP4 1080p"，菜单列所有 `manifest.variants` |
| action_download / _as / _audio / _copy | 主按钮「下载」+ 下拉菜单「下载为 / 仅音频 / 复制链接」 |
| download 进度条 + 停止 | 卡片内进度条 `div_progressbar` 等价物 + 停止按钮 |
| 已下载/失败状态 | 卡片状态机：discovered / downloading / downloaded / failed |

## 11. 明说没查清的点

1. 远程 ruleset 中 `DISABLE_PREVIEW_LOADING` 主机表、站点规则内容（网络下发，本地是空默认）。
2. 服务端授权接口失败分支的精确语义（静态推断）。
3. 少数站点适配器的加密/签名特殊逻辑只做了部分样本确认（canva、osmosis、javrank、xgplayer_crypto）。
