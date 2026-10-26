# Haypi Dragon 兼容性补丁工具 

给老安卓游戏打兼容补丁

 Haypi Dragon（`com.haypi.dragon`）原版 APK 自动应用兼容性补丁、重打包并重新签名的命令行工具。适用于新版 Android 下原版 APK 无法安装、无法启动、加载图片崩溃等问题。

---

## 一、原始安装包

| 项目 | 内容 |
|---|---|
| 应用名 | Haypi Dragon（嗨皮龙） |
| 包名 | `com.haypi.dragon` |
| 目标版本 | 1.6.1（原版，未修改） |
| 文件建议命名 | `com.haypi.dragon.apk` |
| 备注 | 本工具**只针对 1.6.1 原版**做过适配；其它版本会因锚点不匹配而报错 |

> - 测试开发的 apk 来自龙档案馆的资源 (SHA256: `214548926EC4D399675D40D2C0F9A3A3A5091A373966A66422620C5D2211BDC9`)

---

## 二、这个工具做什么

对原版 APK 自动完成 4 处修改：

1. **`AndroidManifest.xml` / `apktool.yml`**
   - `targetSdkVersion` → `22`
   - 增加 `android:requestLegacyExternalStorage="true"`

2. **`NativeUtil.smali`**
   - 将 `HaypiLoadBitmap`、`HaypiLoadBitmapWithFile`、`HaypiSetResourcePath` 三个 `native` 方法替换为 Java 空实现（避免旧 `.so` 在新系统上崩溃）。

3. **`com/haypi/c/d.smali`**
   - 图片加载改用 `Resources.openRawResource` + `BitmapFactory.decodeStream`，并加了异常兜底。

4. **`com/google/android/vending/a/j.smali`**
   - 给 LVL（License Verification Library）的绑定 Intent 显式设置 `setPackage("com.android.vending")`，避免 Android 11+ 包可见性导致绑定失败。

处理完后再执行：

- `zipalign -p 4`
- `apksigner` 签名（v1 + v2 + v3）
- 校验输出包名与 `targetSdkVersion=22`

补丁均带有幂等判断：**已是补丁状态的 APK 再次处理不会重复打补丁**。

---

## 三、要求（依赖）

脚本**不会自动下载依赖**，请提前准备：

| 依赖 | 是否必需 | 说明 / 链接 |
|---|---|---|
| Python 3.8+ | 必需 | 建议 3.10+ |
| JDK 8+ | 必需 | 提供 `java` / `keytool`，（android studio 的 jbr 也有 keytool）(Temurin：<https://adoptium.net/>) （Oracle 的非商业） |
| `apktool.jar`（2.11.1+） | 必需 | <https://github.com/iBotPeaches/Apktool/releases> 下载 `apktool_2.11.1.jar`，重命名为 `apktool.jar`，放入脚本所在目录的 `tools/`（也可用 `APKTOOL_JAR` 环境变量指定） |
| Android SDK **build-tools** | 必需 | 需要 `apksigner`、`zipalign`：<https://developer.android.com/tools/releases/build-tools>；安装后设置 `ANDROID_HOME` / `ANDROID_SDK_ROOT` 或加入 `PATH` |
| `aapt2`(属于sdk的build-tool) | 可选 | 用于校验输出包名 / targetSdk，同属 build-tools |
| `adb`(属于sdk的platform-tools) | 可选 | 自动安装、启动时使用：<https://developer.android.com/tools/releases/platform-tools> |

> - 缺少 `apktool.jar` 或 `apksigner` 时，脚本会直接给出对应链接和期望路径并退出，不会尝试联网下载。
> - android 的 zh地址 ,`developer.android.google.cn/tools/releases/build-tools`

**推荐目录结构：**

```
<任意目录>/
├── main.py
├── tools/
│   └── apktool.jar
├── haypi-mod.keystore      # 首次运行自动生成 （后续一直使用，方可覆盖安装，不然需要先卸载）
└── com.haypi.dragon.apk    # 原版
```

---

## 四、操作步骤

### 1. 交互模式（推荐新手）

```bash
python main.py
```

启动后**把原版 APK 直接拖入终端窗口**（或手动输入路径）后回车，脚本自动完成反编译 → 打补丁 → 重打包 → 对齐 → 签名 → 校验。

### 2. 命令行模式

```bash
# 基本用法，输出为 com.haypi.dragon.patched.apk
python main.py com.haypi.dragon.apk

# 指定输出路径
python main.py com.haypi.dragon.apk -o out/patched.apk

# 打完包后自动安装
python main.py com.haypi.dragon.apk --install

# 先卸载再安装，并启动（会清空应用数据）
python main.py com.haypi.dragon.apk --install --reinstall --launch
```

### 3. 参数说明

| 参数 | 说明 |
|---|---|
| `-o, --out` | 输出 APK 路径，默认原文件名 + `.patched.apk` |
| `--tools-dir` | `apktool.jar` 所在目录，默认脚本目录下的 `tools/` |
| `--keep` | 保留反编译临时目录，便于排查 |
| `--keystore` | 签名 keystore 路径，默认脚本目录下 `haypi-mod.keystore` |
| `--ks-pass` | keystore 密码，默认 `android` |
| `--key-alias` | key alias，默认 `androiddebugkey` |
| `--key-pass` | key 密码，默认同 keystore 密码 |
| `--install` | 完成后执行 `adb install -r` |
| `--reinstall` | 安装前先 `adb uninstall`（**会清除应用数据**），必须配合 `--install` |
| `--launch` | 安装后启动游戏，必须配合 `--install` |

### 4. 输出与校验

成功后会打印类似信息：

```
[4/4] 对齐并签名 ...
  签名方式: apksigner
  Verified using v1 scheme (JAR signing): true
  Verified using v2 scheme (APK Signature Scheme v2): true
  Verified using v3 scheme (APK Signature Scheme v3): true
  targetSdkVersion: 22

完成: com.haypi.dragon.patched.apk (xx.x MB)
```

若找到 `aapt2`，还会自动校验包名仍为 `com.haypi.dragon`、`targetSdkVersion` 为 `22`，不符会直接报错。

---

## 五、注意点（务必阅读）

1. **首次安装必须先卸载原版**
   原版是 Haypi 官方签名，重打包后签名不同，Android 不允许覆盖安装。
   ```bash
   adb uninstall com.haypi.dragon
   ```
   卸载**会清除旧存档**，如需要请先备份 `/sdcard/Android/data/com.haypi.dragon/` 或应用内导出。

2. **targetSdk 降级**
   如果机器上曾装过 `targetSdkVersion=23` 或更高的版本，Android 不允许直接降级到 22，同样需要先卸载。脚本在捕获到 `INSTALL_FAILED_UPDATE_INCOMPATIBLE` 或 `new target SDK ... doesn't support runtime permissions` 时会给出提示。

3. **务必保留 `haypi-mod.keystore`**
   该文件首次运行时会自动生成。**后续更新必须使用同一 keystore**，否则每次都要卸载重装（丢数据）。建议与 APK 一起备份。

4. **MIUI / HyperOS 权限弹窗**
   首次启动会弹出一次旧版权限申请页面(sdk<23会发生>)，点“确定”即可。游戏实际不依赖这些权限，不需要授予。

5. **补丁锚点强匹配**
   脚本按 1.6.1 原版的具体 smali 文本定位补丁位置。非原版会提示：
   ```
   [错误] 补丁锚点未找到: ...
   ```
   此时说明 APK 版本不符或已被修改，请换回原版。

6. **临时目录**
   反编译过程会产生较大临时文件（数百 MB）。脚本会优先选择空间充足的可写目录；空间不足请用 `--keep` 检查或手动清理系统临时目录。

7. **仅客户端**
   本工具只处理**客户端兼容性**。官方登录、对战等服务器已停止服务，联网相关功能需要另做服务端工作，不在本工具范围内。

---

## 六、使用须知

- 用于 较新 Android 系统上重新可运行。
- 用于个人学习 Android 逆向、兼容性调试、备份归档。


---

## 七、常见问题

**Q：报错 `找不到命令: java`**
A：安装 JDK 8+，并把 `java` / `keytool` 所在 `bin` 目录加入 `PATH`，或设置 `JAVA_HOME`。

**Q：报错 `找不到 apksigner`**
A：安装 Android SDK build-tools，并把 `build-tools/<版本>/` 加入 `PATH`，或设置 `ANDROID_HOME` 指向 SDK 根目录。

**Q：报错 `补丁锚点重复 (N 处)`**
A：说明 APK 已被修改或不是 1.6.1 原版，请换回原始安装包。

**Q：安装报 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`**
A：签名不同，先执行 `adb uninstall com.haypi.dragon` 再安装（会丢存档）。

**Q：包打出来了，但游戏启动后还是闪退**
A：用 `--keep` 保留临时目录，把 logcat（`adb logcat`）与反编译结果一起排查；同时确认是否使用了正确的原版 APK。

**Q：keystore 丢了怎么办**
A：无法恢复，只能重新生成并卸载重装（应用数据会丢）。以后请把 `haypi-mod.keystore` 与 APK 一起备份。















