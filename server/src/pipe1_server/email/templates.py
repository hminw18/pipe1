"""Transactional email templates (Korean). Kept in the repository so wording is reviewed and
versioned with the code. Every interpolated value is HTML-escaped."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any

TEMPLATES = {
    "invitation",
    "password_reset",
    "password_changed",
    "email_change_confirm",
    "email_changed_notice",
    "mfa_reset_notice",
}


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text: str
    html: str


def _paragraphs_html(paragraphs: list[str], link: tuple[str, str] | None) -> str:
    body = "".join(f"<p>{escape(p)}</p>" for p in paragraphs)
    if link is not None:
        label, url = link
        body += f'<p><a href="{escape(url, quote=True)}">{escape(label)}</a></p>'
    return (
        '<!doctype html><html lang="ko"><body style="font-family:sans-serif;line-height:1.6">'
        f'{body}<p style="color:#666;font-size:12px">Pipe1</p></body></html>'
    )


def _paragraphs_text(paragraphs: list[str], link: tuple[str, str] | None) -> str:
    lines = list(paragraphs)
    if link is not None:
        lines.append(f"{link[0]}: {link[1]}")
    lines.append("— Pipe1")
    return "\n\n".join(lines)


def render(template: str, data: dict[str, Any]) -> RenderedEmail:
    link: tuple[str, str] | None = None
    if template == "invitation":
        subject = f"[Pipe1] {data['organization_name']} 초대"
        paragraphs = [
            f"{data['organization_name']}에서 Pipe1 계정에 초대했습니다.",
            "아래 링크에서 비밀번호를 설정하세요. 링크는 72시간 동안 한 번만 사용할 수 있습니다.",
        ]
        link = ("초대 수락", data["link"])
    elif template == "password_reset":
        subject = "[Pipe1] 비밀번호 재설정"
        paragraphs = [
            "비밀번호 재설정을 요청하셨습니다.",
            "아래 링크는 30분 동안 한 번만 사용할 수 있습니다.",
            "요청하지 않았다면 이 메일을 무시하세요.",
        ]
        link = ("비밀번호 재설정", data["link"])
    elif template == "password_changed":
        subject = "[Pipe1] 비밀번호가 변경되었습니다"
        paragraphs = [
            "계정의 비밀번호가 변경되었고 다른 기기의 로그인은 모두 종료되었습니다.",
            "본인이 변경하지 않았다면 즉시 관리자에게 알리세요.",
        ]
    elif template == "email_change_confirm":
        subject = "[Pipe1] 이메일 변경 확인"
        paragraphs = [
            "Pipe1 계정의 로그인 이메일을 이 주소로 변경하려면 아래 링크를 여세요.",
            "링크는 30분 동안 한 번만 사용할 수 있습니다.",
        ]
        link = ("이메일 변경 확인", data["link"])
    elif template == "email_changed_notice":
        subject = "[Pipe1] 로그인 이메일이 변경되었습니다"
        paragraphs = [
            f"계정의 로그인 이메일이 {data['new_email_masked']}(으)로 변경되었습니다.",
            "본인이 변경하지 않았다면 즉시 관리자에게 알리세요.",
        ]
    elif template == "mfa_reset_notice":
        subject = "[Pipe1] 2단계 인증이 초기화되었습니다"
        paragraphs = [
            "관리자가 계정의 2단계 인증을 초기화했고 모든 로그인이 종료되었습니다.",
            "다음 로그인 때 인증 앱을 다시 등록하세요. 요청한 적이 없다면 관리자에게 알리세요.",
        ]
    else:
        raise ValueError(f"unknown email template: {template}")
    return RenderedEmail(
        subject=subject,
        text=_paragraphs_text(paragraphs, link),
        html=_paragraphs_html(paragraphs, link),
    )


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}{'*' * max(1, len(local) - len(visible))}@{domain}"
