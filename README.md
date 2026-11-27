# Daily X Briefing Video Generator

参照 `@Leviwoyun` 的极简报刊卡片风格，面向 **X (Twitter)** 的 `4:5`（`1080×1350`）每日 AI 科技 & 美股财经早报短视频一键生成工具。

## 特性
- **零成本免费配音**：使用 `edge-tts`（默认 `zh-CN-YunxiNeural`），自动生成同步中文字幕（ASS 烧录）。
- **4:5 竖屏卡片动效**：HTML/CSS 报刊暖米色排版，通过本地 Chrome 分阶段渲染卡片逐条出现。
- **一键生成**：双击 `generate_today.bat` 或运行 `uv run python build_video.py`，输出直接适配 X 上传的 H.264 MP4。

## 快速使用
1. 一键生成今日视频（自动拉取当日 RSS 头条参考，并生成 `output/YYYY-MM-DD_daily_briefing.mp4`）：
   ```powershell
   uv run python build_video.py
   ```
2. 使用自定义每日 JSON 生成（推荐每天更新 `data/YYYY-MM-DD.json` 后运行）：
   ```powershell
   uv run python build_video.py --input data/sample_briefing.json
   ```
3. 切换配音音色（例如女声 `zh-CN-XiaoxiaoNeural`）：
   ```powershell
   uv run python build_video.py --voice zh-CN-XiaoxiaoNeural --rate +5%
   ```
4. 生成视频并自动发布到 X (Twitter)：
   ```powershell
   uv run python build_video.py --post-to-x
   ```
   或单独发布已有视频（支持 `--dry-run` 预览文案长度）：
   ```powershell
   uv run python post_to_x.py --video output/YYYY-MM-DD_daily_briefing.mp4 --briefing data/YYYY-MM-DD.json --dry-run
   ```

## X (Twitter) 自动发帖配置
需在环境变量或 GitHub Repository Secrets 中配置以下 4 个 OAuth 1.0a 凭据（需在 X Developer Portal 开启 **Read and Write** 权限）：
- `X_API_KEY`
- `X_API_SECRET`
- `X_ACCESS_TOKEN`
- `X_ACCESS_TOKEN_SECRET`

