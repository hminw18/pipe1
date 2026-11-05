"""Fixed choices for manhole fields, shared by the desktop and web apps
(docs/Product_Requirements.md §4.5)."""

from __future__ import annotations

MANHOLE_OPTIONS: dict[str, tuple[str, ...]] = {
    "manhole_type": (
        "NM(표준 맨홀)",
        "SM(소형 맨홀)",
        "IS(역사이펀실)",
        "AR(터널/박스 암거)",
        "OF(배출구)",
        "CP(우수/오수 받이)",
        "UT(유트랩)",
        "OC(기타 맨홀)",
    ),
    "internal_material": (
        "AC(시멘트)",
        "AL(알루미늄)",
        "CI(주철강)",
        "CS(탄소강)",
        "SS(스테인리스강)",
        "PVC(폴리염화비닐)",
        "PE(폴리에틸렌)",
        "PS(폴리에스테르)",
        "PP(폴리프로필렌)",
        "PM(강 보강 플라스틱)",
        "RC(철근 콘크리트)",
        "GRP(유리섬유복합)",
        "Z(기타)",
    ),
    "cover_material": ("주철", "콘크리트", "스테인레스", "기타", "맨홀없음", "BOX관"),
    "invert": ("유", "무"),
    "ladder_shape": (
        "S(한 다리만 디딜 수 있는 길이)",
        "D(두 다리 모두 디딜 수 있는 길이)",
        "T(발가락을 걸칠 수 있는 홈만 존재)",
        "N(발을 디딜 곳이 없는 경우)",
        "Z(기타)",
    ),
}
