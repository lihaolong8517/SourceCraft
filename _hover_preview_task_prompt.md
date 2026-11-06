# 任务：复刻Video Download Helper（VDH）hover悬停预览交互功能

## 项目信息
- 项目路径：`E:\1（开发的视频下载器`
- 主文件：`playwright_browser.py`（PyQt桌面应用）
- 目标类：`PreviewArea`（媒体捕捉面板里的预览卡片，约2336行起）
- 辅助模块：`vdh_capture/preview.py`（预览提取逻辑）
- 当前git分支：`feature/vdh-capture`
- 回滚点tag：`before-vdh-capture-enhance`

---

## VDH的交互效果（要复刻的目标）

打开VDH扩展的媒体列表面板后：

### 默认状态（无hover）
- 每个媒体卡片左边是一块矩形预览区域
- 显示静态缩略图（视频封面）
- 缩略图铺满整个矩形区域
- 没有缩略图时显示默认占位图（不是纯黑）

### 鼠标移上去（hover）
- 缩略图自动切换成视频播放
- 播放一小段预览片段（大概3-6秒）
- 静音播放
- 视频循环播放
- 第一次hover时如果还没生成预览，显示加载状态

### 鼠标移开
- 视频自动暂停
- 自动回到静态缩略图
- 不需要点击，纯hover触发

### 稳定性
- 连续hover/移开10次以上，不黑屏、不崩溃
- 多次hover不会重新下载/提取预览片段（有缓存）

---

## VDH的实现原理（已从源码逆向确认）

### 源码位置
`C:\Users\Administrator\AppData\Local\Microsoft\Edge\User Data\Default\Extensions\jmkaglaafmhbcpleggkmaliipiilhldn\10.6.7.2_0\content\register_components.js`

关键类：`pt`（com-media-preview）

### 核心设计：单video元素模式
**VDH只用一个`<video>`元素，不是img+video两个元素切换**：

```js
// 静态时：用poster属性显示缩略图
this.dom_video.poster = this.state.media.thumbnail_url
  .or(this.state.meta.thumbnail_url)
  .unwrapOr(emptyThumbnailUrl)

// hover时：直接play()
activate() {
  if (已有preview_url) {
    this.dom_video.src = this.preview_url
    this.dom_video.play()
  } else {
    // 发送request_preview消息，后台生成预览
  }
}

// 移开时：只pause()，自动回到poster帧
deactivate() {
  this.playing_p.finally(() => this.dom_video.pause())
}
```

### hover事件触发位置
在com-media组件（卡片）上：
```js
this.addEventListener("mouseenter", () => {
  (this.status == "discovered" || this.status == "downloaded") 
    && this.media_preview.activate()
})
this.addEventListener("mouseleave", () => {
  this.media_preview.deactivate()
})
```

### 预览生成方式
- 后台download worker用内置LibAV WASM（FFmpeg移植版）提取片段
- 生成几秒的小mp4文件
- 存OPFS（浏览器本地文件系统）缓存
- 通过BroadcastChannel通知前端预览就绪

---

## 我们当前的实现（有问题的版本）

### PreviewArea类结构（playwright_browser.py 约2336行起）

```python
class PreviewArea(QWidget):
    def __init__(self, width=176, height=99, parent=None):
        # 两个元素：
        # 1. thumb_label: QLabel - 显示静态缩略图
        # 2. video: QVideoWidget - 播放视频
        # 两个元素叠在一起，切换显示
        
        # 状态：
        # self._spec = None  # 当前媒体信息
        # self._built_path = None  # 预览文件路径
        # self._busy = False  # 是否正在生成预览
        # self._thumb_loaded = False  # 缩略图是否已加载
        
    def set_spec(self, spec): ...  # 设置当前媒体信息
    
    def set_thumbnail_pixmap(self, pixmap): ...  # 设置缩略图
    
    def set_thumbnail_url(self, url): ...  # 从URL加载缩略图
    
    def toggle(self): ...  # 手动点击触发播放/停止
    
    def enterEvent(self, event): ...  # 鼠标悬停事件（当前已关闭自动触发）
    
    def leaveEvent(self, event): ...  # 鼠标移开事件（当前已关闭自动触发）
    
    def _start_build(self): ...  # 启动预览提取线程
    
    def _on_ready(self, path): ...  # 预览提取完成回调
    
    def _on_failed(self, message): ...  # 预览提取失败回调
    
    def _play_built(self): ...  # 播放已提取的预览文件
    
    def stop(self): ...  # 停止播放
    
    def cleanup(self): ...  # 销毁时清理
```

### 当前问题
1. **两个元素切换状态混乱**：thumb_label和video切换显示时，多次hover后黑屏
2. **hover自动触发失败**：片段提取失败（HLS流ffmpeg处理不了），导致悬停即黑屏
3. **缩略图比例不对**：之前改过IgnoreAspectRatio，但可能还有问题
4. **QMediaPlayer状态不稳定**：多次播放后状态异常

### 已经踩过的坑（不要重复试）
1. ❌ 新增独立ThumbnailThread后台线程自动抽帧 → PyQt底层segfault闪退
2. ❌ stop()用player.stop()完全重置 → 第二次播放黑屏
3. ❌ QMediaPlayer直接播放HLS流 → DirectShow错误0x80040266
4. ❌ 直接在_refresh_preview_spec里自动启动线程 → 闪退

---

## 你要实现的效果

### 必须做到（验收标准）
1. ✅ 鼠标移到预览卡片上，自动开始播放视频预览
2. ✅ 鼠标移开，自动停止播放，回到静态缩略图
3. ✅ 连续hover 10次以上不黑屏、不崩溃
4. ✅ 默认显示静态缩略图（网站现成thumbnail_url），不是黑色占位符
5. ✅ 缩略图铺满整个预览区域，不变形、无黑边
6. ✅ 预览片段静音、循环播放

### 约束
1. 只改`playwright_browser.py`里的`PreviewArea`类和`vdh_capture/preview.py`
2. 不能破坏下载器其他功能（下载、嗅探、内置浏览器等）
3. 如果系统ffmpeg提取不了HLS流，优先保证缩略图正常显示，预览尽力而为
4. 不要新增独立的后台线程类（之前试过会闪退）

---

## 调试方法
1. 运行：`python video_downloader.py`
2. 打开内置浏览器，访问视频网站（B站/YouTube等）
3. 打开媒体捕捉面板
4. 鼠标hover到媒体卡片上，观察预览行为
5. 看控制台输出的`[PREVIEW]`错误日志

---

## 建议的实现思路

### 思路1：对齐VDH的单元素模式
不要用QLabel + QVideoWidget两个元素切换。
而是只用QVideoWidget一个元素：
- 静态时显示第一帧（相当于poster效果）
- hover时直接play()
- 移开时pause()，自动回到第一帧

### 思路2：缩略图优先，预览尽力而为
先把缩略图做好（网站现成thumbnail_url自动加载）。
如果预览提取总是失败，就先不做hover自动预览，至少默认状态好看。

### 思路3：用缓存避免重复提取
预览提取一次就缓存起来，多次hover直接播放缓存文件。
（VDH就是这个思路，OPFS缓存）

---

## 回滚方法
如果改坏了，执行：
```bash
git reset --hard before-vdh-capture-enhance
```
