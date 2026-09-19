"""生成「星陨竞技场」的低多边形棋子与标签徽记（SVG）。

设计原则（低多边形 + 一点细节）：
- 统一构图语法：底座 → 腿 → 躯干（明暗两个折面）→ 肩甲 → 头 → 手臂 → 道具 → 细节；
- 靠「手里的东西」区分角色：剑 / 塔盾 / 长弓 / 法杖 / 药瓶 / 晶核 / 多管炮 / 双斧 …
- 靠主色区分星域：怒火橙红、重装钢蓝、迅流青、蚀骨黄绿、幽暗紫、重生青白、守护金、毁灭暗红、平衡灰蓝；
- 全部由多边形拼贴，无描边，用 surface / shade 两个色阶做出水晶折面感；
- 尺寸统一 64×64（角色）与 24×24（标签徽记），缩放不失真。

用法：
    python -m tools.art.generate_assets            # 生成到 frontend/assets 下
    python -m tools.art.generate_assets --out DIR  # 指定输出目录

生成结果是普通 SVG 文件，直接提交进仓库；前端按 /static/assets/... 引用即可，不需要构建步骤。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUT = PROJECT_ROOT / "frontend" / "assets"
SIZE = 64


# ---------------------------------------------------------------- 绘制基础
def polygon(points: str, fill: str, opacity: float | None = None) -> str:
    extra = f' opacity="{opacity}"' if opacity is not None else ""
    return f'<polygon points="{points}" fill="{fill}"{extra}/>'


def circle(cx: float, cy: float, r: float, fill: str, opacity: float | None = None) -> str:
    extra = f' opacity="{opacity}"' if opacity is not None else ""
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}"{extra}/>'


def rect(x: float, y: float, width: float, height: float, fill: str, opacity: float | None = None) -> str:
    extra = f' opacity="{opacity}"' if opacity is not None else ""
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="{fill}"{extra}/>'


def path(d: str, fill: str, opacity: float | None = None) -> str:
    extra = f' opacity="{opacity}"' if opacity is not None else ""
    return f'<path d="{d}" fill="{fill}"{extra}/>'


def group(elements: list[str], *, x: float, y: float, rotate: float = 0.0, scale: float = 1.0) -> str:
    """把一组局部坐标的图形平移到手心位置并旋转 —— 道具都按"握在手里"来画。"""

    transform = f"translate({x:.1f},{y:.1f})"
    if rotate:
        transform += f" rotate({rotate:.1f})"
    if scale != 1.0:
        transform += f" scale({scale:.2f})"
    return f'<g transform="{transform}">' + "".join(elements) + "</g>"


# ---------------------------------------------------------------- 星域配色
@dataclass(frozen=True)
class Palette:
    key: str
    name: str
    base: str      # 主体亮面
    shade: str     # 主体暗面
    light: str     # 高光棱面
    accent: str    # 道具 / 金属
    glow: str      # 发光细节


PALETTES: dict[str, Palette] = {
    "balance": Palette("balance", "平衡星域", "#7C8BA8", "#56617C", "#A9B6CE", "#CBD6E8", "#E8F0FF"),
    "crystal": Palette("crystal", "碎晶星域", "#FF9F45", "#C96C22", "#FFC078", "#FFE0A3", "#FFF3B0"),
    "dark": Palette("dark", "幽暗星域", "#9B6BFF", "#6B45C9", "#C0A6FF", "#E4DAFF", "#D9C9FF"),
    "rebirth": Palette("rebirth", "重生星域", "#7DE2D1", "#4BA99B", "#B4F2E8", "#E6FFFA", "#CFFBF3"),
    "armor": Palette("armor", "重装星域", "#6C8CA8", "#45607A", "#A6C1D6", "#D6E5F0", "#EAF5FF"),
    "guard": Palette("guard", "守护星域", "#F3C05E", "#BE8E36", "#FFDE9A", "#FFF0C8", "#FFF6DC"),
    "swift": Palette("swift", "迅流星域", "#3FD9C4", "#1E9C8C", "#9BF2E5", "#D6FFF8", "#C4FFF5"),
    "rage": Palette("rage", "怒火星域", "#FF6A3D", "#C4471F", "#FFA07A", "#FFD3A8", "#FFE2C4"),
    "corrode": Palette("corrode", "蚀骨星域", "#A9E34B", "#78A82E", "#D3F58F", "#EDFFC7", "#F4FFD9"),
    "ruin": Palette("ruin", "毁灭星域", "#D95060", "#9C2F3D", "#F08B96", "#F6C6CC", "#FFD9DE"),
}


# ---------------------------------------------------------------- 身体骨架
@dataclass
class Build:
    """体型参数：让同类角色也能一眼区分（瘦高 / 敦实 / 宽肩）。"""

    torso_top: int = 26
    torso_bottom: int = 44
    half_width: int = 9        # 躯干半宽
    head_bottom: int = 25
    head_height: int = 10
    shoulder_span: int = 13
    leg_width: int = 4
    base_half: int = 11


BUILDS = {
    "normal": Build(),
    "slim": Build(half_width=7, shoulder_span=11, leg_width=3, base_half=10),
    "heavy": Build(half_width=12, shoulder_span=17, leg_width=6, base_half=13, head_height=9),
    "tiny": Build(torso_top=30, torso_bottom=46, half_width=8, shoulder_span=12, leg_width=4, base_half=10, head_height=8),
    "lanky": Build(torso_top=22, torso_bottom=44, half_width=8, shoulder_span=12, leg_width=3, base_half=10, head_height=11),
}


def body(palette: Palette, build: Build, *, cloak: bool = False, spike_shoulders: bool = False) -> list[str]:
    """通用躯干：底座、腿、躯干折面、肩甲、头、面甲、手臂。"""

    c = SIZE // 2
    parts: list[str] = []

    # 底座
    parts.append(
        polygon(
            f"{c - build.base_half},{build.torso_bottom + 10} {c + build.base_half},{build.torso_bottom + 10} "
            f"{c + build.base_half + 4},{build.torso_bottom + 14} {c - build.base_half - 4},{build.torso_bottom + 14}",
            "#2A3350",
        )
    )

    if cloak:
        parts.append(
            polygon(
                f"{c - build.half_width - 5},{build.torso_top - 1} {c + build.half_width + 5},{build.torso_top - 1} "
                f"{c + build.half_width + 9},{build.torso_bottom + 12} {c - build.half_width - 9},{build.torso_bottom + 12}",
                palette.shade,
            )
        )

    # 腿
    leg_h = build.torso_bottom + 12 - build.torso_bottom
    for direction in (-1, 1):
        x = c + direction * (build.half_width // 2 + 1)
        parts.append(
            polygon(
                f"{x - build.leg_width},{build.torso_bottom} {x + build.leg_width // 2},{build.torso_bottom} "
                f"{x + build.leg_width // 2},{build.torso_bottom + leg_h - 2} {x - build.leg_width},{build.torso_bottom + leg_h - 2}",
                palette.shade,
            )
        )

    # 躯干：暗面 + 亮面 + 左侧高光棱
    parts.append(
        polygon(
            f"{c - build.half_width},{build.torso_top} {c + build.half_width},{build.torso_top} "
            f"{c + build.half_width - 2},{build.torso_bottom} {c - build.half_width + 2},{build.torso_bottom}",
            palette.shade,
        )
    )
    parts.append(
        polygon(
            f"{c - build.half_width + 1},{build.torso_top + 1} {c + 1},{build.torso_top + 1} "
            f"{c + 1},{build.torso_bottom - 1} {c - build.half_width + 3},{build.torso_bottom - 1}",
            palette.base,
        )
    )
    parts.append(
        polygon(
            f"{c - build.half_width + 1},{build.torso_top + 1} {c - build.half_width + 4},{build.torso_top + 1} "
            f"{c - build.half_width + 3},{build.torso_bottom - 1} {c - build.half_width + 1},{build.torso_bottom - 1}",
            palette.light,
            opacity=0.75,
        )
    )

    # 肩甲
    for direction in (-1, 1):
        x = c + direction * (build.half_width - 1)
        outer = x + direction * 6
        parts.append(
            polygon(
                f"{x - direction * 2},{build.torso_top - 2} {outer},{build.torso_top - 4} "
                f"{outer + direction * 1},{build.torso_top + 6} {x - direction * 1},{build.torso_top + 5}",
                palette.base,
            )
        )
        if spike_shoulders:
            parts.append(
                polygon(
                    f"{outer},{build.torso_top - 4} {outer + direction * 5},{build.torso_top - 9} "
                    f"{outer + direction * 1},{build.torso_top - 3}",
                    palette.accent,
                )
            )

    # 头 + 面甲
    hb = build.head_bottom
    htop = hb - build.head_height
    parts.append(
        polygon(
            f"{c - 5},{htop} {c + 5},{htop} {c + 7},{hb} {c + 4},{hb + 2} {c - 4},{hb + 2} {c - 7},{hb}",
            palette.base,
        )
    )
    parts.append(polygon(f"{c - 5},{htop + 3} {c + 5},{htop + 3} {c + 5},{htop + 6} {c - 5},{htop + 6}", palette.accent, 0.85))
    parts.append(polygon(f"{c - 5},{htop} {c - 2},{htop} {c - 4},{htop + 9} {c - 6},{hb}", palette.light, 0.6))

    # 手臂
    for direction in (-1, 1):
        x = c + direction * (build.half_width - 1)
        parts.append(
            polygon(
                f"{x - 1},{build.torso_top + 4} {x + direction * 3},{build.torso_top + 4} "
                f"{x + direction * 3},{build.torso_bottom - 2} {x - 1},{build.torso_bottom - 2}",
                palette.shade,
            )
        )
    return parts


# ---------------------------------------------------------------- 角色道具
# 全部按「握在手里」的局部坐标绘制：原点 = 手心，-y 朝上，再由 group() 平移 + 旋转到位。
def _sword(p: Palette, *, length: int = 26, wide: bool = False, guard: bool = True) -> list[str]:
    w = 2.2 if not wide else 3.2
    parts = [
        polygon(f"{-w},{-length} {w},{-length} {w - 1},0 {-w + 1},0", p.accent),
        polygon(f"{-w},{-length} {-w + 1},{-length} {-w + 1},0 {-w},0", p.glow, 0.6),
    ]
    if guard:
        parts.append(rect(-6, 0, 12, 2.4, p.shade))
    parts.append(rect(-1.6, 2.4, 3.2, 7, p.shade))
    return parts


def _great_blade(p: Palette) -> list[str]:
    return [
        polygon("-5,-30 5,-30 8,-6 -2,6", p.accent),
        polygon("-5,-30 -1,-30 -2,6 -5,-2", p.glow, 0.55),
        rect(-8, 6, 16, 3, p.shade),
        rect(-2, 9, 4, 6, p.shade),
    ]


def _spear(p: Palette) -> list[str]:
    return [
        rect(-1.8, -22, 3.6, 40, p.shade),
        polygon("-5,-22 5,-22 0,-38", p.accent),
        polygon("-5,-22 0,-22 0,-34", p.glow, 0.5),
    ]


def _hammer(p: Palette) -> list[str]:
    return [
        rect(-1.8, -20, 3.6, 34, p.shade),
        polygon("-9,-28 9,-28 9,-16 -9,-16", p.accent),
        polygon("-9,-28 -3,-28 -3,-16 -9,-16", p.glow, 0.5),
    ]


def _axe(p: Palette, *, double: bool = False, heavy: bool = False) -> list[str]:
    blade_h = 15 if heavy else 12
    parts = [
        rect(-1.8, -20, 3.6, 34, p.shade),
        polygon(f"2,-20 13,-27 15,{-27 + blade_h} 2,{-20 + blade_h}", p.accent),
        polygon("2,-20 7,-23 6,-8 2,-8", p.glow, 0.45),
    ]
    if double:
        parts.append(polygon(f"-2,-20 -13,-27 -15,{-27 + blade_h} -2,{-20 + blade_h}", p.accent))
    return parts


def _execute_axe(p: Palette) -> list[str]:
    return [
        rect(-1.8, -22, 3.6, 36, p.shade),
        polygon("-14,-30 -2,-25 -2,-6 -16,-6", p.accent),
        polygon("-14,-30 -8,-27 -8,-6 -16,-6", p.glow, 0.45),
    ]


def _round_shield(p: Palette, *, r: float = 8.5) -> list[str]:
    import math

    outer, inner = [], []
    for i in range(8):
        angle = math.pi / 8 + i * math.pi / 4
        outer.append(f"{r * math.cos(angle):.1f},{r * math.sin(angle):.1f}")
        inner.append(f"{r * 0.55 * math.cos(angle):.1f},{r * 0.55 * math.sin(angle):.1f}")
    return [polygon(" ".join(outer), p.accent), polygon(" ".join(inner), p.base)]


def _tower_shield(p: Palette, *, w: float = 9, h: float = 24) -> list[str]:
    return [
        polygon(f"{-w},{-h / 2} {w},{-h / 2} {w},{h / 2 - 5} 0,{h / 2} {-w},{h / 2 - 5}", p.accent),
        polygon(f"{-w + 2.5},{-h / 2 + 2.5} {w - 2.5},{-h / 2 + 2.5} {w - 2.5},{h / 2 - 6} "
                f"0,{h / 2 - 3} {-w + 2.5},{h / 2 - 6}", p.base),
        polygon(f"{-w + 2.5},{-h / 2 + 2.5} 0,{-h / 2 + 2.5} 0,{h / 2 - 3} {-w + 2.5},{h / 2 - 6}", p.light, 0.55),
    ]


def _bow(p: Palette, *, h: float = 19, d: float = 7) -> list[str]:
    return [
        path(f"M 0,{-h} Q {d},0 0,{h} L -2,{h - 2} Q {d - 4},0 -2,{-h + 2} Z", p.accent),
        polygon(f"-2,{-h + 2} -2,{h - 2} -5,{h} -5,{-h}", p.glow, 0.5),
    ]


def _staff(p: Palette, *, hook: bool = False, lantern: bool = False) -> list[str]:
    parts = [rect(-1.8, -24, 3.6, 36, p.shade)]
    if hook:
        parts.append(
            path("M 0,-24 Q 11,-32 6,-40 L 2,-38 Q 6,-31 -2,-26 Z", p.accent)
        )
        parts.append(polygon("5,-42 12,-45 9,-36", p.glow))
    if lantern:
        parts.append(polygon("-6,-22 6,-22 5,-10 -5,-10", p.accent))
        parts.append(polygon("-2.5,-19 2.5,-19 2,-13 -2,-13", p.glow))
        parts.append(rect(-1.6, -28, 3.2, 6, p.shade))
    return parts


def _vial(p: Palette) -> list[str]:
    return [
        polygon("-5,-3 5,-3 6,7 -6,7", p.glow),
        rect(-2, -10, 4, 7, p.shade),
        polygon("-3,-4 3,-4 3,2 -3,2", p.accent, 0.8),
    ]


def _claw(p: Palette) -> list[str]:
    """单手短刃（噬魂者左右手各一把）。"""

    return [
        polygon("0,-4 7,-9 5,10 -1,12", p.accent),
        polygon("0,-4 3,-6 2,10 -1,12", p.glow, 0.5),
    ]


def _cannon(p: Palette) -> list[str]:
    parts = [polygon("-15,-7 15,-7 13,7 -13,7", p.shade)]
    for offset in (-8, 0, 8):
        parts.append(rect(offset - 3, -20, 6, 14, p.accent))
        parts.append(rect(offset - 1.5, -20, 3, 14, p.glow, 0.55))
    return parts


def _core(p: Palette) -> list[str]:
    """自爆步兵怀里那颗不稳定晶核（身体层面，不走手心）。"""

    return [
        polygon("27,31 37,31 41,39 32,48 23,39", p.accent),
        polygon("32,31 37,31 41,39 32,48", p.shade),
        polygon("29,34 34,34 32,41", p.glow, 0.85),
        path("M 39,44 Q 47,48 43,56 L 37,54 Q 42,49 36,46 Z", p.shade),
    ]


def _crystal_wall(p: Palette) -> list[str]:
    """晶壁守卫：身前三层半透明晶壁（偏左侧，避免把整个身体盖住）。"""

    parts = []
    for i, offset in enumerate((0, 4, 8)):
        parts.append(
            polygon(
                f"{8 + offset},26 {16 + offset},17 {24 + offset},26 {22 + offset},44 {12 + offset},44",
                p.base if i % 2 == 0 else p.light,
                0.8 - i * 0.18,
            )
        )
    return parts


def _spike_ring(p: Palette) -> list[str]:
    parts = []
    for angle_deg in range(0, 360, 45):
        import math

        a = math.radians(angle_deg)
        x1 = 32 + 16 * math.cos(a)
        y1 = 36 + 16 * math.sin(a)
        x2 = 32 + 24 * math.cos(a)
        y2 = 36 + 24 * math.sin(a)
        x3 = 32 + 16 * math.cos(a + 0.35)
        y3 = 36 + 16 * math.sin(a + 0.35)
        parts.append(polygon(f"{x1:.1f},{y1:.1f} {x2:.1f},{y2:.1f} {x3:.1f},{y3:.1f}", p.accent, 0.9))
    return parts


def _souls(p: Palette) -> list[str]:
    return [
        circle(20, 20, 3, p.glow, 0.75),
        circle(46, 16, 2.4, p.glow, 0.6),
        circle(38, 10, 2, p.glow, 0.5),
    ]


def _cape(p: Palette, build: Build) -> list[str]:
    c = SIZE // 2
    return [
        polygon(
            f"{c - build.half_width - 2},{build.torso_top} {c - build.half_width - 8},{build.torso_bottom + 10} "
            f"{c - 4},{build.torso_bottom + 6} {c - 6},{build.torso_top}",
            p.shade,
        )
    ]


# ---------------------------------------------------------------- 角色数据
@dataclass
class Piece:
    char_id: int
    slug: str
    name: str
    domain: str
    build: str = "normal"
    cloak: bool = False
    spike_shoulders: bool = False
    props: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)


def pieces() -> list[Piece]:
    """20 名角色：道具决定剪影，配色决定星域。"""

    return [
        Piece(1, "balance-a", "均衡战士A", "balance", "slim", props=["sword"]),
        Piece(2, "balance-b", "均衡战士B", "balance", "normal", props=["sword", "round_shield"]),
        Piece(3, "balance-c", "均衡战士C", "balance", "normal", props=["tower_shield"]),
        Piece(4, "self-destruct", "自爆步兵", "crystal", "tiny", props=["core"]),
        Piece(5, "curse-wizard", "诅咒巫师", "dark", "lanky", props=["hook_staff"], extras=["runes"]),
        Piece(6, "necromancer", "死灵法师", "rebirth", "slim", props=["lantern_staff"], extras=["souls"]),
        Piece(7, "iron-guard", "铁甲卫士", "armor", "heavy", props=["hammer"]),
        Piece(8, "shield-bearer", "护盾部署者", "guard", "heavy", props=["great_shield"]),
        Piece(9, "wind-archer", "风行射手", "swift", "lanky", props=["bow"], extras=["quiver"]),
        Piece(10, "berserker", "狂战士", "rage", "normal", spike_shoulders=True, props=["double_axe"]),
        Piece(11, "poison-thrower", "毒药投手", "corrode", "slim", props=["vial"], extras=["fumes"]),
        Piece(12, "artillery", "重炮统领", "ruin", "heavy", props=["cannon"]),
        Piece(13, "balance-d", "均衡战士D", "balance", "normal", props=["spear"]),
        Piece(14, "balance-e", "均衡战士E", "balance", "heavy", props=["tower_shield"], extras=["low_attack"]),
        Piece(15, "shadow-hunter", "暗影猎手", "swift", "normal", cloak=True, props=["short_bow"], extras=["cape"]),
        Piece(16, "blood-berserker", "血怒斗士", "rage", "normal", spike_shoulders=True, props=["great_blade"], extras=["cracks"]),
        Piece(17, "crystal-guard", "晶壁守卫", "armor", "heavy", props=["crystal_wall"]),
        Piece(18, "soul-eater", "噬魂者", "dark", "slim", props=["claws"], extras=["souls"]),
        Piece(19, "thorn-guard", "荆棘守卫", "guard", "heavy", props=["tower_shield", "spike_ring"]),
        Piece(20, "executioner", "处决者", "ruin", "normal", props=["execute_axe"]),
    ]


def render_piece(piece: Piece) -> str:
    palette = PALETTES[piece.domain]
    build = BUILDS[piece.build]
    layers = body(palette, build, cloak=piece.cloak, spike_shoulders=piece.spike_shoulders)

    hand_y = build.torso_top + 12
    hand_right_x = SIZE // 2 + build.half_width + 5
    hand_left_x = SIZE // 2 - build.half_width - 5
    # 手心连接点：让道具看起来是"握着"的，而不是浮在旁边
    hands = [
        circle(hand_right_x, hand_y, 3.2, palette.accent),
        circle(hand_left_x, hand_y + 2, 3.2, palette.accent),
    ]
    held: list[str] = []
    body_level: list[str] = []

    for prop in piece.props:
        if prop == "sword":
            held.append(group(_sword(palette, length=25), x=hand_right_x, y=hand_y, rotate=15))
        elif prop == "round_shield":
            held.append(group(_round_shield(palette), x=hand_left_x, y=hand_y + 2, rotate=-4))
        elif prop == "tower_shield":
            held.append(group(_tower_shield(palette), x=hand_left_x - 5, y=hand_y + 1, rotate=-8))
        elif prop == "core":
            body_level.append("".join(_core(palette)))
        elif prop == "hook_staff":
            held.append(group(_staff(palette, hook=True), x=hand_right_x + 1, y=hand_y + 4, rotate=8))
        elif prop == "lantern_staff":
            held.append(group(_staff(palette, lantern=True), x=hand_right_x + 1, y=hand_y + 4, rotate=6))
        elif prop == "hammer":
            held.append(group(_hammer(palette), x=hand_right_x, y=hand_y + 2, rotate=12))
        elif prop == "great_shield":
            held.append(group(_tower_shield(palette, w=10, h=26), x=SIZE // 2 - 9, y=hand_y + 1, rotate=-10))
        elif prop == "bow":
            held.append(group(_bow(palette), x=hand_right_x, y=hand_y - 2, rotate=4))
        elif prop == "short_bow":
            held.append(group(_bow(palette, h=15, d=6), x=hand_right_x, y=hand_y, rotate=4))
        elif prop == "double_axe":
            held.append(group(_axe(palette, double=True), x=hand_right_x, y=hand_y + 4, rotate=12))
        elif prop == "great_blade":
            held.append(group(_great_blade(palette), x=hand_right_x, y=hand_y + 2, rotate=16))
        elif prop == "vial":
            held.append(group(_vial(palette), x=hand_right_x, y=hand_y - 6, rotate=-8))
        elif prop == "cannon":
            held.append(group(_cannon(palette), x=SIZE // 2 + 9, y=build.torso_top + 13, rotate=-14))
        elif prop == "spear":
            held.append(group(_spear(palette), x=hand_right_x, y=hand_y + 4, rotate=20))
        elif prop == "claws":
            held.append(group(_claw(palette), x=hand_right_x - 1, y=hand_y, rotate=14))
            held.append(group(_claw(palette), x=hand_left_x + 1, y=hand_y + 2, rotate=-14))
        elif prop == "crystal_wall":
            body_level.append("".join(_crystal_wall(palette)))
        elif prop == "spike_ring":
            body_level.append("".join(_spike_ring(palette)))
        elif prop == "execute_axe":
            held.append(group(_execute_axe(palette), x=hand_right_x, y=hand_y + 2, rotate=10))

    layers += hands + held + body_level

    for extra in piece.extras:
        if extra == "runes":
            layers += [
                polygon(f"{hand_right_x + 4},{hand_y - 26} {hand_right_x + 9},{hand_y - 22} "
                        f"{hand_right_x + 4},{hand_y - 18} {hand_right_x - 1},{hand_y - 22}", palette.glow, 0.85),
                polygon(f"{hand_right_x + 8},{hand_y - 12} {hand_right_x + 12},{hand_y - 9} "
                        f"{hand_right_x + 8},{hand_y - 6} {hand_right_x + 4},{hand_y - 9}", palette.glow, 0.6),
            ]
        elif extra == "souls":
            layers += _souls(palette)
        elif extra == "quiver":
            layers += [
                rect(hand_left_x - 6, hand_y - 8, 6, 16, palette.shade),
                polygon(f"{hand_left_x - 6},{hand_y - 8} {hand_left_x + 2},{hand_y - 12} {hand_left_x + 1},{hand_y - 6}",
                        palette.accent),
            ]
        elif extra == "fumes":
            layers += [
                circle(50, 8, 2.6, palette.glow, 0.55),
                circle(45, 6, 2, palette.glow, 0.4),
                circle(54, 13, 1.8, palette.glow, 0.35),
            ]
        elif extra == "cape":
            layers.insert(0, _cape(palette, build)[0])
        elif extra == "cracks":
            layers += [
                path("M 28 30 L 34 36 L 29 40 L 35 46", "none"),
                polygon("29,30 33,36 30,39 34,44 31,44 27,38 30,35", palette.glow, 0.75),
            ]
        elif extra == "low_attack":
            layers += [
                polygon(f"{hand_left_x - 10},{hand_y - 16} {hand_left_x - 4},{hand_y - 12} "
                        f"{hand_left_x - 10},{hand_y - 8}", palette.accent, 0.6),
            ]

    content = "\n  ".join(layers)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}" '
        f'role="img" aria-label="{piece.name}">\n'
        f"  <!-- {piece.name} · {PALETTES[piece.domain].name} -->\n  {content}\n</svg>\n"
    )


# ---------------------------------------------------------------- 标签徽记
TAG_EMBLEMS: dict[str, tuple[str, str]] = {
    "explosive": ("自爆", "burst"),
    "necromancy": ("复活", "revive"),
    "curse": ("标签剥夺", "broken_ring"),
    "shield": ("护盾", "shield"),
    "heavy_armor": ("重装盔甲", "plates"),
    "pierce": ("箭矢穿透", "pierce"),
    "berserk": ("狂暴", "flame"),
    "poison": ("中毒", "drop"),
    "aoe": ("群伤", "burst_wave"),
    "lifesteal": ("吸血", "leech"),
    "thorns": ("反弹", "reflect"),
    "execute": ("斩杀", "slash"),
}


def render_tag(key: str) -> str:
    name, shape = TAG_EMBLEMS[key]
    glow = "#E9F3FF"
    base = "#7C8BA8"
    accent = "#67E8F9"
    shapes: dict[str, str] = {
        "burst": polygon("12,2 14,9 21,7 16,12 22,16 14,15 12,22 10,15 2,16 8,12 3,7 10,9", accent),
        "revive": polygon("12,3 17,10 13.5,10 13.5,17 10.5,17 10.5,10 7,10", glow)
        + circle(12, 19, 4, accent, 0.6),
        "broken_ring": path("M 12 3 A 9 9 0 1 1 5 17 L 8 15 A 6 6 0 1 0 12 6 Z", accent),
        "shield": polygon("12,2 21,6 21,13 12,22 3,13 3,6", accent)
        + polygon("12,6 17,8.5 17,12.5 12,17.5 7,12.5 7,8.5", base),
        "plates": polygon("4,4 20,4 20,9 4,9", accent)
        + polygon("4,10 20,10 20,15 4,15", glow, 0.75)
        + polygon("4,16 20,16 20,21 4,21", base),
        "pierce": polygon("2,14 14,14 14,18 2,18", base, 0.8) + polygon("12,6 22,12 12,18", accent),
        "flame": path("M 12 2 Q 19 10 17 15 Q 15 21 12 22 Q 9 21 7 15 Q 5 10 12 2 Z", accent)
        + path("M 12 10 Q 14 14 12 18 Q 10 14 12 10 Z", glow, 0.8),
        "drop": polygon("12,2 17,12 12,22 7,12", accent) + circle(9, 7, 1.6, glow, 0.7),
        "burst_wave": circle(12, 12, 2.6, glow)
        + path("M 12 4 A 8 8 0 0 1 20 12", accent)
        + path("M 12 20 A 8 8 0 0 1 4 12", accent)
        + path("M 12 0 A 12 12 0 0 1 24 12", accent, 0.6)
        + path("M 12 24 A 12 12 0 0 1 0 12", accent, 0.6),
        "leech": polygon("12,22 7,12 10,12 10,4 14,4 14,12 17,12", accent)
        + circle(18, 6, 2, glow, 0.7),
        "reflect": path("M 4 6 L 12 12 L 4 18 Z", accent)
        + path("M 20 6 L 12 12 L 20 18 Z", accent, 0.7),
        "slash": path("M 3 4 L 15 12 L 3 20 Z", accent) + path("M 21 4 L 15 12 L 21 20 Z", glow, 0.8),
    }
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24" '
        f'role="img" aria-label="{name}">\n  <!-- {name} -->\n  {shapes[shape]}\n</svg>\n'
    )


# ---------------------------------------------------------------- 输出
def main() -> None:
    parser = argparse.ArgumentParser(description="生成角色棋子与标签徽记")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    pieces_dir = args.out / "pieces"
    tags_dir = args.out / "tags"
    pieces_dir.mkdir(parents=True, exist_ok=True)
    tags_dir.mkdir(parents=True, exist_ok=True)

    for piece in pieces():
        target = pieces_dir / f"piece-{piece.char_id:02d}.svg"
        target.write_text(render_piece(piece), encoding="utf-8")
    for key in TAG_EMBLEMS:
        (tags_dir / f"tag-{key}.svg").write_text(render_tag(key), encoding="utf-8")

    print(f"生成 {len(pieces())} 个棋子 → {pieces_dir}")
    print(f"生成 {len(TAG_EMBLEMS)} 个标签徽记 → {tags_dir}")


if __name__ == "__main__":
    main()
