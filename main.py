from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

APKTOOL_VERSION = "2.11.1"
APKTOOL_RELEASES = "https://github.com/iBotPeaches/Apktool/releases"
JDK_URL = "https://adoptium.net/"
BUILD_TOOLS_URL = "https://developer.android.com/tools/releases/build-tools"
PLATFORM_TOOLS_URL = "https://developer.android.com/tools/releases/platform-tools"

PACKAGE_NAME = "com.haypi.dragon"
KS_PASS_DEFAULT = "android"
KEY_ALIAS_DEFAULT = "androiddebugkey"

NATIVE_LOAD_BITMAP_OLD = """.method public static native HaypiLoadBitmap([BLandroid/graphics/Bitmap;)Z
.end method"""
NATIVE_LOAD_BITMAP_NEW = """.method public static HaypiLoadBitmap([BLandroid/graphics/Bitmap;)Z
    .locals 1

    const/4 v0, 0x0

    return v0
.end method"""

NATIVE_LOAD_FILE_OLD = """.method public static native HaypiLoadBitmapWithFile(Ljava/lang/String;Landroid/graphics/Bitmap;I)Z
.end method"""
NATIVE_LOAD_FILE_NEW = """.method public static HaypiLoadBitmapWithFile(Ljava/lang/String;Landroid/graphics/Bitmap;I)Z
    .locals 1

    const/4 v0, 0x0

    return v0
.end method"""

NATIVE_SET_PATH_OLD = """.method public static native HaypiSetResourcePath(Ljava/lang/String;I)V
.end method"""
NATIVE_SET_PATH_NEW = """.method public static HaypiSetResourcePath(Ljava/lang/String;I)V
    .locals 0

    return-void
.end method"""

IMAGE_OLD = """    :cond_3
    iget-object v0, v1, Landroid/util/TypedValue;->string:Ljava/lang/CharSequence;

    invoke-interface {v0}, Ljava/lang/CharSequence;->toString()Ljava/lang/String;

    move-result-object v0

    invoke-static {p0, v0, p3}, Lcom/haypi/c/d;->a(Landroid/content/Context;Ljava/lang/String;Landroid/graphics/Bitmap;)Landroid/graphics/Bitmap;

    move-result-object p3"""

IMAGE_NEW = """    :cond_3
    :try_start_dec
    invoke-virtual {p0}, Landroid/content/Context;->getResources()Landroid/content/res/Resources;

    move-result-object v0

    invoke-virtual {v0, p1}, Landroid/content/res/Resources;->openRawResource(I)Ljava/io/InputStream;

    move-result-object v0

    invoke-static {v0}, Landroid/graphics/BitmapFactory;->decodeStream(Ljava/io/InputStream;)Landroid/graphics/Bitmap;

    move-result-object v0

    if-nez v0, :cond_dec_ok

    goto :goto_0

    :cond_dec_ok
    move-object p3, v0
    :try_end_dec
    .catch Ljava/lang/Exception; {:try_start_dec .. :try_end_dec} :catch_dec

    goto :goto_0

    :catch_dec
    move-exception v0"""

LICENSE_OLD = """    invoke-direct {v2, v3}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V

    const/4 v3, 0x1"""

LICENSE_NEW = """    invoke-direct {v2, v3}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V

    const-string v3, "com.android.vending"

    invoke-virtual {v2, v3}, Landroid/content/Intent;->setPackage(Ljava/lang/String;)Landroid/content/Intent;

    move-result-object v3

    const/4 v3, 0x1"""


class PatchError(RuntimeError):
    pass


def run(cmd, cwd=None):
    try:
        proc = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise PatchError(f"找不到命令: {cmd[0]}") from exc
    if proc.returncode != 0:
        lines = proc.stdout.strip().splitlines()
        tail = "\n".join(lines[-40:])
        raise PatchError(f"命令执行失败 ({proc.returncode}): {' '.join(str(c) for c in cmd)}\n{tail}")
    return proc.stdout


def tool_cmd(tool):
    tool = str(tool)
    if os.name == "nt" and tool.lower().endswith((".bat", ".cmd")):
        return ["cmd", "/c", tool]
    return [tool]


def find_java():
    home = os.environ.get("JAVA_HOME")
    if home:
        name = "java.exe" if os.name == "nt" else "java"
        candidate = Path(home) / "bin" / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which("java")


def find_keytool():
    home = os.environ.get("JAVA_HOME")
    if home:
        name = "keytool.exe" if os.name == "nt" else "keytool"
        candidate = Path(home) / "bin" / name
        if candidate.is_file():
            return str(candidate)
    return shutil.which("keytool")


def find_adb():
    found = shutil.which("adb")
    if found:
        return found
    for root in sdk_roots():
        name = "adb.exe" if os.name == "nt" else "adb"
        candidate = root / "platform-tools" / name
        if candidate.is_file():
            return str(candidate)
    return None


def sdk_roots():
    roots = []
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(var)
        if value:
            roots.append(Path(value))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.append(Path(local) / "Android" / "Sdk")
        roots.append(Path.home() / "AppData" / "Local" / "Android" / "Sdk")
    else:
        roots.append(Path.home() / "Android" / "Sdk")
        roots.append(Path("/usr/lib/android-sdk"))
    adb = shutil.which("adb")
    if adb:
        roots.append(Path(adb).resolve().parent.parent)
    result = []
    seen = set()
    for root in roots:
        try:
            key = str(root.resolve()).lower()
        except OSError:
            key = str(root).lower()
        if key not in seen and root.is_dir():
            seen.add(key)
            result.append(root)
    return result


def version_key(path):
    parts = re.findall(r"\d+", path.name)
    return tuple(int(p) for p in parts)


def find_build_tool(name):
    names = [name]
    if os.name == "nt":
        names = [name, name + ".exe", name + ".bat"]
    else:
        names = [name, name + ".sh"]
    for root in sdk_roots():
        build_tools = root / "build-tools"
        if not build_tools.is_dir():
            continue
        versions = sorted([p for p in build_tools.iterdir() if p.is_dir()], key=version_key, reverse=True)
        for version in versions:
            for candidate_name in names:
                candidate = version / candidate_name
                if candidate.is_file():
                    return str(candidate)
    for candidate_name in names:
        found = shutil.which(candidate_name)
        if found:
            return found
    return None


def ensure_apktool(tools_dir):
    env_jar = os.environ.get("APKTOOL_JAR")
    candidates = []
    if env_jar:
        candidates.append(Path(env_jar))
    script_dir = Path(__file__).resolve().parent
    candidates.extend(
        [
            tools_dir / "apktool.jar",
            script_dir / "apktool.jar",
            script_dir / "tools" / "apktool.jar",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    expected = tools_dir / "apktool.jar"
    raise PatchError(
        "找不到 apktool.jar。\n"
        f"1. 打开 {APKTOOL_RELEASES}\n"
        f"2. 下载 apktool_{APKTOOL_VERSION}.jar\n"
        f"3. 改名为 apktool.jar，放到: {expected}\n"
        "也可以用环境变量 APKTOOL_JAR 指定 jar 的完整路径。"
    )


def ensure_keystore(path, keytool, ks_pass, alias, key_pass):
    if path.is_file():
        return
    if not keytool:
        raise PatchError(
            f"找不到 keytool（随 JDK 提供）。请安装 JDK 8+ 并把 java/keytool 加入 PATH：{JDK_URL}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"生成签名密钥: {path}")
    run(
        tool_cmd(keytool)
        + [
            "-genkeypair",
            "-keystore",
            str(path),
            "-alias",
            alias,
            "-keyalg",
            "RSA",
            "-keysize",
            "2048",
            "-validity",
            "10000",
            "-storepass",
            ks_pass,
            "-keypass",
            key_pass,
            "-dname",
            "CN=Haypi Dragon Mod, O=HaypiMod, C=CN",
        ]
    )


def replace_once(text, old, new, label):
    count = text.count(old)
    if count == 1:
        return text.replace(old, new, 1)
    if count == 0:
        if new in text:
            return text
        raise PatchError(f"补丁锚点未找到: {label}。这个 APK 可能不是原版 1.6.1。")
    raise PatchError(f"补丁锚点重复 ({count} 处): {label}。请使用原版 APK。")


def patch_manifest(text):
    if "requestLegacyExternalStorage" in text:
        return text
    match = re.search(r"<application(?=\s|>)", text)
    if not match:
        raise PatchError("AndroidManifest.xml 中找不到 <application> 标签。")
    return text[: match.end()] + ' android:requestLegacyExternalStorage="true"' + text[match.end() :]


def patch_yml(text):
    existing = re.search(r"^(\s*targetSdkVersion\s*:\s*).*$", text, re.MULTILINE)
    if existing:
        return text[: existing.start()] + existing.group(1) + "22" + text[existing.end() :]
    min_sdk = re.search(r"^(\s*)minSdkVersion\s*:\s*(\d+)\s*$", text, re.MULTILINE)
    if min_sdk:
        insert_at = min_sdk.end()
        return text[:insert_at] + f"\n{min_sdk.group(1)}targetSdkVersion: 22" + text[insert_at:]
    sdk_info = re.search(r"^sdkInfo\s*:\s*$", text, re.MULTILINE)
    if sdk_info:
        insert_at = sdk_info.end()
        return text[:insert_at] + "\n  minSdkVersion: 8\n  targetSdkVersion: 22" + text[insert_at:]
    raise PatchError("apktool.yml 中找不到 sdkInfo/minSdkVersion。")


def patch_native_util(path):
    text = path.read_text(encoding="utf-8")
    if ".method public static HaypiLoadBitmap(" in text:
        return False
    text = replace_once(text, NATIVE_LOAD_BITMAP_OLD, NATIVE_LOAD_BITMAP_NEW, "NativeUtil.HaypiLoadBitmap")
    text = replace_once(text, NATIVE_LOAD_FILE_OLD, NATIVE_LOAD_FILE_NEW, "NativeUtil.HaypiLoadBitmapWithFile")
    text = replace_once(text, NATIVE_SET_PATH_OLD, NATIVE_SET_PATH_NEW, "NativeUtil.HaypiSetResourcePath")
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def patch_image_loader(path):
    text = path.read_text(encoding="utf-8")
    if ":try_start_dec" in text:
        return False
    text = replace_once(text, IMAGE_OLD, IMAGE_NEW, "com/haypi/c/d 图片解码")
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def patch_license_intent(path):
    text = path.read_text(encoding="utf-8")
    if 'setPackage(Ljava/lang/String;)Landroid/content/Intent;' in text and '"com.android.vending"' in text:
        return False
    text = replace_once(text, LICENSE_OLD, LICENSE_NEW, "LVL 显式 Intent")
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def strip_signature_files(path):
    with zipfile.ZipFile(path, "r") as source:
        signature_names = []
        for info in source.infolist():
            upper = info.filename.upper()
            if upper.startswith("META-INF/") and upper.endswith((".SF", ".RSA", ".DSA", ".EC")):
                signature_names.append(info.filename)
        if not signature_names:
            return
        tmp = path.with_suffix(path.suffix + ".nosig")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                if info.filename in signature_names:
                    continue
                data = source.read(info.filename)
                target.writestr(info, data)
    tmp.replace(path)


def sign_apk(unsigned, output, keystore, apksigner, zipalign, ks_pass, alias, key_pass):
    if not apksigner:
        raise PatchError(
            "找不到 apksigner，无法签名。\n"
            f"请安装 Android SDK build-tools：{BUILD_TOOLS_URL}\n"
            "安装后把 build-tools 里的 apksigner 加入 PATH，"
            "或设置 ANDROID_HOME 指向 SDK 根目录。"
        )
    strip_signature_files(unsigned)
    source = unsigned
    if zipalign:
        aligned = unsigned.with_name("aligned.apk")
        run(tool_cmd(zipalign) + ["-f", "-p", "4", str(unsigned), str(aligned)])
        source = aligned
    run(
        tool_cmd(apksigner)
        + [
            "sign",
            "--ks",
            str(keystore),
            "--ks-key-alias",
            alias,
            "--ks-pass",
            f"pass:{ks_pass}",
            "--key-pass",
            f"pass:{key_pass}",
            "--min-sdk-version",
            "8",
            "--v1-signing-enabled",
            "true",
            "--v2-signing-enabled",
            "true",
            "--v3-signing-enabled",
            "true",
            "--out",
            str(output),
            str(source),
        ]
    )
    return "apksigner"


def verify_apk(output, apksigner, aapt2):
    if apksigner:
        text = run(tool_cmd(apksigner) + ["verify", "--print-certs", str(output)])
        for line in text.splitlines():
            if line.startswith("Verified using") or "Signer #1 certificate DN" in line:
                print("  " + line.strip())
    if aapt2:
        text = run(tool_cmd(aapt2) + ["dump", "badging", str(output)])
        target = re.search(r"targetSdkVersion:'(\d+)'", text)
        package = re.search(r"package: name='([^']+)'", text)
        if package and package.group(1) != PACKAGE_NAME:
            raise PatchError(f"输出 APK 包名不是 {PACKAGE_NAME}: {package.group(1)}")
        if target and target.group(1) != "22":
            raise PatchError(f"输出 APK targetSdkVersion={target.group(1)}，预期 22。")
        if target:
            print(f"  targetSdkVersion: {target.group(1)}")


def free_mb(path):
    try:
        return shutil.disk_usage(path).free // (1024 * 1024)
    except OSError:
        return 0


def choose_temp_root(input_path):
    candidates = [
        Path(tempfile.gettempdir()),
        input_path.parent,
        Path(__file__).resolve().parent,
        Path.cwd(),
    ]
    seen = set()
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            pass
        key = str(candidate).lower()
        if key in seen or not candidate.is_dir():
            continue
        seen.add(key)
        probe = candidate / f".haypi_write_test_{os.getpid()}"
        try:
            probe.write_bytes(b"x")
            probe.unlink()
        except OSError:
            continue
        if free_mb(candidate) >= 800:
            return candidate
    return Path(tempfile.gettempdir())


def resolve_output(input_path, out_arg):
    if out_arg:
        output = Path(out_arg).expanduser()
        if not output.is_absolute():
            output = Path.cwd() / output
        return output
    name = input_path.stem
    if not name.endswith(".patched"):
        name += ".patched"
    return input_path.with_name(name + ".apk")


def prompt_path():
    print("Haypi Dragon APK Patcher")
    print("把原版 com.haypi.dragon.apk 拖进这个窗口，或输入路径后回车。")
    print("输入 q 退出。")
    while True:
        try:
            raw = input("APK: ")
        except EOFError:
            raise SystemExit(0)
        raw = raw.strip().lstrip("\ufeff").strip()
        if raw.lower() in {"q", "quit", "exit"}:
            raise SystemExit(0)
        raw = raw.strip('"').strip("'").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if path.is_file():
            return path
        print("找不到文件，请重新输入。")


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="给 Haypi Dragon 1.6.1 原版 APK 应用兼容性补丁并重新签名。",
        epilog=(
            "不带参数运行会进入交互模式，可直接拖入 APK。\n\n"
            "依赖下载：\n"
            f"  apktool: {APKTOOL_RELEASES}\n"
            f"           下载 apktool_{APKTOOL_VERSION}.jar，改名为 apktool.jar 放到 tools 目录\n"
            f"  JDK 8+:  {JDK_URL}\n"
            f"  build-tools (apksigner/zipalign): {BUILD_TOOLS_URL}\n"
            f"  adb (可选): {PLATFORM_TOOLS_URL}"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("apk", nargs="?", help="原版 APK 路径")
    parser.add_argument("-o", "--out", help="输出 APK 路径")
    parser.add_argument("--tools-dir", default=str(Path(__file__).resolve().parent / "tools"), help="工具目录（放 apktool.jar）")

    parser.add_argument("--keep", action="store_true", help="保留临时工作目录")
    parser.add_argument("--keystore", default=str(Path(__file__).resolve().parent / "haypi-mod.keystore"), help="签名 keystore 路径")
    parser.add_argument("--ks-pass", default=KS_PASS_DEFAULT, help="keystore 密码")
    parser.add_argument("--key-alias", default=KEY_ALIAS_DEFAULT, help="key alias")
    parser.add_argument("--key-pass", default=None, help="key 密码（默认同 keystore 密码）")
    parser.add_argument("--install", action="store_true", help="完成后用 adb 安装")
    parser.add_argument("--reinstall", action="store_true", help="安装前先卸载旧版（会清除应用数据）")
    parser.add_argument("--launch", action="store_true", help="安装后启动游戏")
    args = parser.parse_args(argv)
    if args.key_pass is None:
        args.key_pass = args.ks_pass
    if args.reinstall and not args.install:
        parser.error("--reinstall 必须和 --install 一起使用")
    if args.launch and not args.install:
        parser.error("--launch 必须和 --install 一起使用")
    return args


def main(argv=None):
    args = parse_args(argv)
    interactive = args.apk is None
    input_path = prompt_path() if interactive else Path(args.apk).expanduser().resolve()
    if not input_path.is_file():
        raise PatchError(f"APK 不存在: {input_path}")
    if input_path.suffix.lower() != ".apk":
        raise PatchError("输入文件不是 .apk")

    output = resolve_output(input_path, args.out)
    if output.resolve() == input_path.resolve():
        raise PatchError("输出路径不能和输入路径相同。")

    java = find_java()
    if not java:
        raise PatchError(f"找不到 java。请安装 JDK 8+ 并加入 PATH：{JDK_URL}")
    keytool = find_keytool()
    apktool = ensure_apktool(Path(args.tools_dir).expanduser())
    apksigner = find_build_tool("apksigner")
    if not apksigner:
        raise PatchError(
            "找不到 apksigner，无法签名。\n"
            f"请安装 Android SDK build-tools：{BUILD_TOOLS_URL}\n"
            "安装后把 build-tools 里的 apksigner 加入 PATH，"
            "或设置 ANDROID_HOME 指向 SDK 根目录。"
        )
    zipalign = find_build_tool("zipalign")
    aapt2 = find_build_tool("aapt2")

    print(f"输入: {input_path}")
    print(f"输出: {output}")
    print("签名: apksigner")

    keystore = Path(args.keystore).expanduser()
    ensure_keystore(keystore, keytool, args.ks_pass, args.key_alias, args.key_pass)

    temp_root = choose_temp_root(input_path)
    work_root = Path(tempfile.mkdtemp(prefix="haypi_patch_", dir=str(temp_root)))
    work_dir = work_root / "decoded"
    unsigned = work_root / "unsigned.apk"
    try:
        local_apk = work_root / "input.apk"
        shutil.copy2(input_path, local_apk)

        print("[1/4] 反编译 APK ...")
        run(tool_cmd(java) + ["-jar", str(apktool), "d", "-f", "-o", str(work_dir), str(local_apk)])

        print("[2/4] 应用补丁 ...")
        manifest = work_dir / "AndroidManifest.xml"
        yml = work_dir / "apktool.yml"
        native_util = work_dir / "smali" / "com" / "haypi" / "framework" / "util" / "NativeUtil.smali"
        image_loader = work_dir / "smali" / "com" / "haypi" / "c" / "d.smali"
        license_intent = work_dir / "smali" / "com" / "google" / "android" / "vending" / "a" / "j.smali"
        for required in (manifest, yml, native_util, image_loader, license_intent):
            if not required.is_file():
                raise PatchError(f"反编译结果缺少文件: {required}。这个 APK 可能不是 Haypi Dragon 1.6.1。")

        manifest.write_text(patch_manifest(manifest.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
        yml.write_text(patch_yml(yml.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
        changed = {
            "NativeUtil": patch_native_util(native_util),
            "图片解码": patch_image_loader(image_loader),
            "LVL Intent": patch_license_intent(license_intent),
        }
        for label, did_change in changed.items():
            print(f"  {label}: {'已修改' if did_change else '已是补丁状态'}")

        print("[3/4] 重新打包 ...")
        run(tool_cmd(java) + ["-jar", str(apktool), "b", str(work_dir), "-o", str(unsigned)])

        print("[4/4] 对齐并签名 ...")
        output.parent.mkdir(parents=True, exist_ok=True)
        signer = sign_apk(
            unsigned,
            output,
            keystore,
            apksigner,
            zipalign,
            args.ks_pass,
            args.key_alias,
            args.key_pass,
        )
        output.with_name(output.name + ".idsig").unlink(missing_ok=True)
        print(f"  签名方式: {signer}")
        verify_apk(output, apksigner, aapt2)
        size_mb = output.stat().st_size / (1024 * 1024)
        print(f"\n完成: {output} ({size_mb:.1f} MB)")
        print("首次启动时 MIUI 会弹出旧版权限页面，点“确定”即可，不需要开启任何权限。")
        print("如果提示签名冲突或 targetSdk 降级，请先卸载旧版再安装：")
        print(f"  adb uninstall {PACKAGE_NAME}")
        print(f"  adb install -r \"{output}\"")
        print(f"keystore: {keystore}（以后更新请保留同一个文件）")

        if args.install:
            adb = find_adb()
            if not adb:
                raise PatchError("找不到 adb，无法自动安装。")
            if args.reinstall:
                run([adb, "uninstall", PACKAGE_NAME])
            try:
                run([adb, "install", "-r", str(output)])
            except PatchError as exc:
                text = str(exc)
                if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in text or "signatures do not match" in text:
                    raise PatchError(
                        "安装失败：新 APK 与已安装版本的签名不同。\n"
                        f"请先执行: adb uninstall {PACKAGE_NAME}\n"
                        "然后重新运行本脚本并加 --install（会清除旧应用数据）。"
                    ) from exc
                if "new target SDK" in text and "doesn't support runtime permissions" in text:
                    raise PatchError(
                        "安装失败：不能从 targetSdk 23 的版本直接降级到 22。\n"
                        f"请先执行: adb uninstall {PACKAGE_NAME}\n"
                        "然后重新运行本脚本并加 --install（会清除旧应用数据）。"
                    ) from exc
                raise
            print("安装完成。")
            if args.launch:
                run([adb, "shell", "am", "start", "-n", f"{PACKAGE_NAME}/.DragonLogoActivity"])

        if interactive:
            try:
                input("按回车退出 ...")
            except EOFError:
                pass
    finally:
        if args.keep:
            print(f"临时目录: {work_root}")
        else:
            shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except PatchError as exc:
        print(f"\n[错误] {exc}", file=sys.stderr)
        if len(sys.argv) == 1:
            try:
                input("按回车退出 ...")
            except EOFError:
                pass
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        sys.exit(130)

