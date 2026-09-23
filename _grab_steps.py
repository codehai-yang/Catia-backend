"""把案例用到的两个 CATPart 导出成 STEP 存到 _tmp_branch/，供离线复跑验证。

只读操作：ExportData 到本地文件，不改动 CATIA 里的任何内容。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import pythoncom  # noqa: E402
from win32com.client import GetActiveObject  # noqa: E402

WORK = ROOT / "_tmp_branch"
OUT = ROOT / "_grab.txt"
L: list[str] = []


def log(msg: str = "") -> None:
    L.append(str(msg))
    print(msg)


TARGETS = {
    "MultiBranchable1.CATPart": WORK / "harness_full.stp",
    "clipA.CATPart": WORK / "clipA_full.stp",
}


def main() -> None:
    pythoncom.CoInitialize()
    app = GetActiveObject("CATIA.Application")
    doc = app.ActiveDocument
    log(f"active doc = {doc.Name}")

    sel = doc.Selection
    log(f"selection count = {sel.Count2}")

    docs = app.Documents
    log("")
    log(f"open documents ({docs.Count}):")
    found: dict[str, object] = {}
    for i in range(1, docs.Count + 1):
        d = docs.Item(i)
        try:
            name = str(d.Name)
        except Exception as exc:  # noqa: BLE001
            name = f"<err {exc}>"
        log(f"  {name}")
        if name in TARGETS:
            found[name] = d

    log("")
    for name, path in TARGETS.items():
        if name not in found:
            log(f"  {name}: 未打开，无法导出")
            continue
        try:
            found[name].ExportData(str(path), "stp")
            log(f"  {name} -> {path.name}  ({path.stat().st_size} 字节)")
        except Exception as exc:  # noqa: BLE001
            log(f"  {name}: 导出失败 {exc}")

    OUT.write_text("\n".join(L), encoding="utf-8")
    print("written", OUT)


if __name__ == "__main__":
    main()
