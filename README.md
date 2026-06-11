# 桌宠精灵

这是一个完整桌宠小工具：可以导入已有 spritesheet 播放，也可以在软件里直接调用 OpenAI Images API 生成动作帧、GIF 预览和 spritesheet。

## 运行

```bash
pip install -r requirements.txt
python app.py
```

程序需要 `tkinter`、`Pillow`、`numpy` 和 `openai`。有些 conda/minimal Python 环境不带 `tkinter`；python.org 的标准 Python 通常自带。

## 播放已有精灵图

1. 打开 `播放` 页。
2. 点击 `导入精灵图`。
3. 如果同名 JSON 存在，程序会自动加载。
4. 如果没有 JSON，设置格宽、格高、行数、列数、FPS，然后点击 `按网格重建动作`。
5. 选择一行动作，修改动作名称、帧数、FPS、是否循环，然后点击 `应用修改`。
6. 双击动作，或点击 `显示桌宠`，即可在桌面显示。
7. 左键拖动桌宠位置，右键打开隐藏/关闭菜单。

播放页支持为每个宠物设置昵称和交互绑定。昵称留空时，宠物库卡片显示宠物文件夹名。

默认交互：

```text
待机 -> idle
摸摸 -> wave
左拖 -> move-left
右拖 -> move-right
拎起 -> lift
点击 -> idle
玩耍 -> play
```

`玩耍` 会在用户一段时间没有交互时自动插播一次，然后回到待机。

## 后台运行和系统托盘

- 点击主窗口右上角关闭按钮不会退出程序，只会隐藏主窗口。
- 最小化主窗口会自动收起到系统托盘，桌宠继续在后台播放。
- 托盘菜单提供：
  - `显示主窗口`
  - `显示桌宠`
  - `隐藏桌宠`
  - `退出`
- 只有点击托盘菜单里的 `退出` 才会真正关闭程序。

## 直接生成桌宠

1. 打开 `生成` 页。
2. 用一句话描述你想要的桌宠，例如：`生成一个开心的小猫桌宠，会待机、挥手、跳起来`。
3. 填写 `API Key`，也可以提前设置环境变量 `OPENAI_API_KEY`。
4. 如需自定义 API 地址，填写 `API URL`，例如 `https://api.openai.com/v1`。
5. 点击 `开始生成`。
6. 程序会自动解析动作，生成日志会实时显示当前步骤。
7. 生成完成后，程序会自动加载 `final/spritesheet.png`。

历史任务下拉框默认选择 `新任务`。保持 `新任务` 时点击 `开始生成` 会创建新的生成目录；选择已有历史任务后点击 `开始生成` 会从该任务未完成的步骤继续。

生成时会强制包含桌宠基础交互动作：`idle`、`wave`、`move-left`、`move-right`、`lift`、`play`。生成流程里的 `decoded/base.png` 只是内部 canonical base 形象，用来锁定角色身份，不是一个动作。
切分帧时会自动按透明主体包围盒统一角色视觉大小，避免不同动作切换时忽大忽小。

程序会保存上一条指令、API Key、API URL、模型、最后加载的桌宠和桌宠开启状态。下次打开会自动复用。

## 目录结构

```text
desktop-pet-app/
  app.py
  config/
    app_config.json
  pets/
    20260610-203000-a1b2c3/
      frames/
      previews/
      spritesheet.png
      spritesheet.webp
      spritesheet.json
  output/
    20260610-203000-a1b2c3/
      final/
```

- `config/`：保存软件配置。
- `pets/`：桌宠库，每个宠物一个文件夹，软件启动时会自动读取，播放页会以缩略图卡片展示，点击 `选择` 即可应用。
- `output/`：生成工作目录，每次生成使用 `时间戳+随机字符串`，不再写入 `latest`。
- 生成完成后，程序会自动把 `output/<run-id>/final/` 复制到 `pets/<run-id>/`，并应用这个新宠物。

## 可选打包 EXE

仓库不依赖固定的本地打包脚本。需要发布 Windows 单文件程序时，可以在自己的 Python 环境里安装 PyInstaller 后打包：

```powershell
python -m pip install -r requirements.txt
python -m PyInstaller `
  --noconfirm `
  --onefile `
  --windowed `
  --name DesktopPet `
  --add-data "generator;generator" `
  app.py
```

打包命令会把 `generator/` 生成器一起放进 exe。不同平台的 `--add-data` 分隔符不同：Windows 使用 `源;目标`，macOS/Linux 使用 `源:目标`。

打包完成后，发布目录是：

```text
dist/
  DesktopPet.exe
  config/
  output/
  pets/
```

双击 `DesktopPet.exe` 即可运行。外部只需要保留 `DesktopPet.exe`、`config/`、`output/`、`pets/`；生成器不需要单独放在外面。

默认动作示例：

```text
idle,wave,jump,cheer,think,work,focus,move-right,move-left
```

每个宠物文件夹会保留：

```text
frames/
previews/
spritesheet.png
spritesheet.webp
spritesheet.json
```

## JSON 格式

```json
{
  "version": 1,
  "image": "spritesheet.png",
  "cell": { "width": 192, "height": 208 },
  "actions": [
    { "id": "idle", "name": "待机", "row": 0, "frames": 6, "fps": 8, "loop": true },
    { "id": "wave", "name": "挥手", "row": 1, "frames": 6, "fps": 8, "loop": true }
  ],
  "anchor": { "x": 96, "y": 208 },
  "scale": 1.0
}
```
