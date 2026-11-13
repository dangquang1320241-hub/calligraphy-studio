import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# webapp 的数据根在 import 时读取：测试统一用临时目录，绝不触碰真实档案
_WEB_ROOT = Path(tempfile.mkdtemp(prefix="cs_web_test_"))
os.environ["CALLIGRAPHY_DATA_ROOT"] = str(_WEB_ROOT)

from core.profile import build_profile  # noqa: E402


@pytest.fixture
def synthetic_source(tmp_path):
    """合成作品：白纸 + 4 列 × 3 字块（黑方块，列距/字距明显），确定性检测。"""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (1200, 700), (235, 230, 215))
    d = ImageDraw.Draw(im)
    for xc in (980, 760, 540, 320):        # 右→左 4 列
        for yc in (140, 300, 460):         # 每列 3 个字块
            d.rectangle([xc - 35, yc, xc + 35, yc + 80], fill=(15, 15, 15))
    p = tmp_path / "作品.jpg"
    im.save(p, quality=92)
    return p


@pytest.fixture
def profile_factory(synthetic_source):
    """在临时数据根里构建档案的工厂。返回 (data_root, build函数)。"""
    def build(slug="测试作品", text=None, **kw):
        root = Path(kw.pop("data_root", None) or tempfile.mkdtemp(prefix="cs_root_"))
        build_profile(synthetic_source, text, slug, root, **kw)
        return root
    return build


@pytest.fixture
def built_profile(profile_factory):
    """带 12 字释文的已建档案：text 4 列 × 3 字。"""
    root = profile_factory(text="甲乙丙，丁戊己。庚辛壬；癸子丑")
    return root / "作品档案" / "测试作品"
