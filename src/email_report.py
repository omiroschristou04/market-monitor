"""Email delivery for the morning market briefing.

Sends a clean, mobile-friendly summary email (NOT the full HTML report) over
Gmail's SMTP server using STARTTLS on port 587. The email shows a dark-navy
header, the market-regime badge and VIX level, the seven analyst briefing
bullets, and an "Open Full Report" button that opens the full HTML report from
the local Desktop. Credentials are read from a local ``.env`` file and never
hardcoded.

--------------------------------------------------------------------------- #
 Gmail setup (one-time)
--------------------------------------------------------------------------- #
Gmail no longer allows plain-password SMTP logins, so you must create an
"App Password" (a 16-character token tied to your account):

  1. Enable 2-Step Verification on your Google account:
        https://myaccount.google.com/security
  2. Once 2-Step Verification is on, open:
        https://myaccount.google.com/apppasswords
  3. Choose app "Mail" and device "Other" (name it e.g. "Market Monitor"),
     then click Generate. Google shows a 16-character password.
  4. Copy that password (remove the spaces) into your local ``.env`` file as
     GMAIL_APP_PASSWORD. See ``.env.example`` for the exact variable names.

Then create a ``.env`` file next to ``run.py`` (copy ``.env.example``):

    GMAIL_ADDRESS=youraddress@gmail.com
    GMAIL_APP_PASSWORD=abcd efgh ijkl mnop   -> store WITHOUT spaces
    EMAIL_RECIPIENT=whoever@example.com      -> optional; defaults to sender

The ``.env`` file holds secrets — keep it out of version control.
"""

import html
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr

from . import analysis

# Sentinel value shipped in .env.example / .env until the user fills it in.
PLACEHOLDER_PASSWORD = "REPLACE_WITH_YOUR_APP_PASSWORD"

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is listed in requirements.txt
    load_dotenv = None

# Gmail SMTP endpoint (STARTTLS).
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

# Where the report lives on this machine. The "Open Full Report" link is a
# local file:// URL, so clicking it from this PC opens the report directly.
DESKTOP_FILE_BASE = "file:///C:/Users/ALEX/Desktop/"

# Navy palette for the header / regime strip (mirrors the report's masthead).
NAVY = "#0a1628"
NAVY_STRIP = "#0d2137"

# Semantic tone -> colour, matching the report's visual language.
TONE_COLOURS = {
    "pos": "#16a34a",      # green   — positive
    "neg": "#dc2626",      # red     — negative
    "caution": "#d97706",  # amber   — caution
    "neutral": "#475569",  # slate   — neutral
}


def _vix_regime(vix_value):
    """Map a VIX level to a (label, colour) volatility-regime pair."""
    if vix_value is None:
        return ("UNKNOWN", "#94a3b8")
    if vix_value < 15:
        return ("LOW VOL", "#16a34a")
    if vix_value < 20:
        return ("NORMAL", "#22c55e")
    if vix_value < 30:
        return ("ELEVATED", "#d97706")
    return ("STRESS", "#dc2626")


def _load_env():
    """Load variables from a local .env file if python-dotenv is available."""
    if load_dotenv is not None:
        load_dotenv()


def _report_date(report_path):
    """Readable date for the subject, parsed from market_report_YYYYMMDD.html."""
    stamp = os.path.splitext(os.path.basename(report_path))[0]
    stamp = stamp.replace("market_report_", "")
    try:
        return datetime.strptime(stamp, "%Y%m%d").strftime("%d %B %Y")
    except ValueError:
        return stamp or datetime.now().strftime("%d %B %Y")


def _report_url(report_path):
    """URL the "Open Full Report" button points at.

    Prefers the public GitHub Pages site (``PAGES_URL`` in ``.env``) so
    the report opens on any device. Falls back to a local ``file://`` URL that
    opens the report from this PC's Desktop when the Pages URL is not set.
    """
    pages_url = os.environ.get("PAGES_URL", "").strip()
    if pages_url:
        return pages_url

    filename = os.path.basename(report_path)
    return DESKTOP_FILE_BASE + filename


def _vix_value(rows):
    """Pull the raw VIX level out of the metric rows (None if missing)."""
    if not rows:
        return None
    for r in rows:
        if r.get("ticker") == "^VIX":
            return r.get("price")
    return None


def _plain_text_fallback(report_date, regime, vix_value, bullets, report_url):
    """A clean text/plain alternative for clients that block HTML."""
    lines = [
        "MORNING MARKET BRIEFING",
        report_date,
        "",
    ]
    if regime is not None:
        lines.append(f"Market Regime: {regime['label']}")
    if vix_value is not None:
        vix_label, _ = _vix_regime(vix_value)
        # Same one-decimal rendering the report uses, so the email and the
        # briefing it links to never quote the same level two ways.
        lines.append(f"VIX: {analysis._vix_str(vix_value)} ({vix_label})")
    lines.append("")
    for b in bullets or []:
        lines.append(f"- {b['label']}: {b['text']}")
    lines.extend([
        "",
        f"Open the full report: {report_url}",
        "",
        f"{report_date} — Generated by Market Monitor",
        "",
    ])
    return "\n".join(lines)


def _bullets_html(bullets):
    """Render the seven briefing bullets as an email-safe table."""
    rows_html = []
    for i, b in enumerate(bullets):
        dot = TONE_COLOURS.get(b.get("tone"), TONE_COLOURS["neutral"])
        border = "" if i == 0 else "border-top:1px solid #eef1f6;"
        rows_html.append(
            "<tr>"
            f'<td width="20" valign="top" style="padding:9px 10px 9px 0;{border}">'
            f'<div style="width:9px;height:9px;border-radius:50%;'
            f'background:{dot};margin-top:5px;"></div></td>'
            f'<td valign="top" style="padding:9px 0;{border}font-size:14px;'
            f'color:#334155;line-height:1.5;">'
            f'<strong style="color:#0f172a;">{html.escape(b["label"])}:</strong> '
            f'{html.escape(b["text"])}</td>'
            "</tr>"
        )
    return "".join(rows_html)


def _build_email_html(report_date, regime, vix_value, bullets, report_url):
    """Build the clean, mobile-friendly summary email body."""
    regime_colour = (TONE_COLOURS.get(regime["tone"], TONE_COLOURS["neutral"])
                     if regime else TONE_COLOURS["neutral"])
    regime_label = html.escape(regime["label"]) if regime else "Unknown"

    vix_label, vix_colour = _vix_regime(vix_value)
    vix_display = (analysis._vix_str(vix_value) if vix_value is not None
                   else "&mdash;")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Morning Market Briefing</title>
</head>
<body style="margin:0;padding:0;background:#eef1f6;
    font-family:'Segoe UI',-apple-system,BlinkMacSystemFont,Roboto,Helvetica,Arial,sans-serif;
    -webkit-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
    style="background:#eef1f6;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
    style="max-width:600px;width:100%;background:#ffffff;border-radius:14px;
    overflow:hidden;box-shadow:0 4px 16px rgba(15,23,42,.10);">

    <!-- Header -->
    <tr><td style="background:{NAVY};padding:30px 28px 24px;">
        <div style="color:#ffffff;font-size:24px;font-weight:700;
            font-family:Georgia,'Times New Roman',serif;line-height:1.2;">
            Morning Market Briefing</div>
        <div style="color:#93c5fd;font-size:12px;letter-spacing:1.4px;
            text-transform:uppercase;margin-top:10px;">{html.escape(report_date)}</div>
    </td></tr>

    <!-- Regime + VIX strip -->
    <tr><td style="background:{NAVY_STRIP};padding:18px 28px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
            <td valign="middle" style="text-align:left;">
                <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;
                    letter-spacing:1px;margin-bottom:7px;">Market Regime</div>
                <span style="display:inline-block;padding:7px 16px;border-radius:999px;
                    background:{regime_colour};color:#ffffff;font-weight:700;
                    font-size:14px;letter-spacing:.3px;">{regime_label}</span>
            </td>
            <td valign="middle" style="text-align:right;">
                <div style="font-size:10px;color:#94a3b8;text-transform:uppercase;
                    letter-spacing:1px;margin-bottom:7px;">VIX</div>
                <span style="color:#ffffff;font-size:20px;font-weight:700;
                    vertical-align:middle;">{vix_display}</span>
                <span style="color:{vix_colour};font-size:12px;font-weight:700;
                    vertical-align:middle;margin-left:6px;">{vix_label}</span>
            </td>
        </tr></table>
    </td></tr>

    <!-- Briefing bullets -->
    <tr><td style="padding:26px 28px 6px;">
        <div style="font-size:12px;font-weight:700;color:#64748b;
            text-transform:uppercase;letter-spacing:1.2px;margin-bottom:8px;">
            Analyst Briefing</div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            {_bullets_html(bullets)}
        </table>
    </td></tr>

    <!-- Open Full Report button -->
    <tr><td align="center" style="padding:18px 28px 30px;">
        <a href="{html.escape(report_url, quote=True)}"
            style="display:inline-block;background:#2563eb;color:#ffffff;
            text-decoration:none;font-weight:700;font-size:15px;
            padding:14px 36px;border-radius:8px;">Open Full Report</a>
    </td></tr>

    <!-- Footer -->
    <tr><td style="background:#f8fafc;border-top:1px solid #e2e8f0;
        padding:20px 28px;text-align:center;">
        <div style="color:#94a3b8;font-size:12px;">{html.escape(report_date)}</div>
        <div style="color:#94a3b8;font-size:12px;margin-top:5px;">
            Generated by Market Monitor</div>
    </td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def send_email(report_path, subject=None, rows=None):
    """Send a clean summary email for the report at *report_path*.

    Instead of embedding the full HTML report, this builds a concise,
    mobile-friendly email (navy header, regime badge + VIX, the seven analyst
    briefing bullets, and an "Open Full Report" button linking to the local
    report on the Desktop). *rows* are the metric dicts from fetch, used to
    derive the regime, VIX and bullets; if omitted the email still sends with
    just the header and the report link.

    Returns True on success, False if it was skipped or failed. Missing
    credentials are treated as a soft skip (warn, return False) so the daily
    pipeline never crashes just because email is not configured.
    """
    _load_env()

    sender = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipient = os.environ.get("EMAIL_RECIPIENT", "").strip() or sender

    if not sender or not password:
        print("      ! Email not configured (set GMAIL_ADDRESS and "
              "GMAIL_APP_PASSWORD in .env); skipping email.")
        print("        See .env.example and the setup notes in "
              "src/email_report.py.")
        return False

    # The .env ships with a placeholder password — make the skip obvious.
    if password == PLACEHOLDER_PASSWORD:
        print("      ! GMAIL_APP_PASSWORD is still the placeholder "
              f"('{PLACEHOLDER_PASSWORD}'); skipping email.")
        print("        Create a Gmail App Password and paste it into .env to "
              "enable delivery.")
        print("        Guide: https://myaccount.google.com/apppasswords "
              "(see src/email_report.py for full steps).")
        return False

    if not os.path.isfile(report_path):
        print(f"      ! Report not found at {report_path}; cannot email.")
        return False

    report_date = _report_date(report_path)
    report_url = _report_url(report_path)

    # Derive the regime, VIX and the seven briefing bullets from the day's
    # data. If rows are missing we still send a useful email (header + link).
    regime = analysis.market_regime(rows) if rows else None
    vix = _vix_value(rows)
    bullets = analysis.morning_briefing(rows)["bullets"] if rows else []

    html_body = _build_email_html(report_date, regime, vix, bullets, report_url)

    if subject is None:
        subject = f"Morning Market Briefing — {report_date}"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("Market Monitor", sender))
    msg["To"] = recipient
    # Plain-text fallback first, then the clean HTML summary as the primary body.
    msg.set_content(
        _plain_text_fallback(report_date, regime, vix, bullets, report_url))
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(sender, password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        print("      ! Gmail rejected the login. Check GMAIL_ADDRESS and that "
              "GMAIL_APP_PASSWORD is a valid 16-char App Password (not your "
              "normal password).")
        return False
    except Exception as exc:  # network / TLS / send failures
        print(f"      ! Failed to send email: {exc}")
        return False

    print(f"      Email sent to {recipient}.")
    return True
