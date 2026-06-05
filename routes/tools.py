import uuid
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from middleware.auth_guard import get_current_user
from utils.feature_gate import check_plan
from utils.zip_builder import build_website_zip
from services.supabase_service import get_supabase_admin
from services import openai_service

router = APIRouter()

# ── Request models ──────────────────────────────────────────────────────────


class WebsiteGenerateRequest(BaseModel):
    business_name: str
    business_type: str
    tagline: str = ""
    color_scheme: str = "dark gold"


class WebsiteExportRequest(BaseModel):
    project_id: str


class SocialPostRequest(BaseModel):
    platform: str = Field(..., examples=["instagram", "facebook", "twitter", "linkedin", "tiktok"])
    topic: str
    tone: str = "friendly"
    business_name: str


class HashtagRequest(BaseModel):
    niche: str
    topic: str
    count: int = Field(default=20, ge=5, le=30)


class BrandingRequest(BaseModel):
    category: str
    keywords: str
    vibe: str


# ── Website ─────────────────────────────────────────────────────────────────


@router.post("/website/generate")
async def generate_website(
    body: WebsiteGenerateRequest,
    current_user: dict = Depends(get_current_user),
):
    plan = current_user["plan"]
    is_pro = plan in ("pro", "agency")

    # Build HTML — watermarked for free users, full for pro+
    html = _build_html(body, watermark=not is_pro)
    css = _build_css(body.color_scheme)

    project_content = {
        "business_name": body.business_name,
        "business_type": body.business_type,
        "tagline": body.tagline,
        "color_scheme": body.color_scheme,
        "primary_color": _resolve_primary(body.color_scheme),
        "html": html,
        "css": css,
    }

    supabase = get_supabase_admin()
    project_id = str(uuid.uuid4())
    supabase.table("projects").insert({
        "id": project_id,
        "user_id": current_user["user_id"],
        "name": body.business_name,
        "type": "website",
        "status": "preview" if is_pro else "draft",
        "content": project_content,
    }).execute()

    return {
        "project_id": project_id,
        "plan": plan,
        "watermarked": not is_pro,
        "html": html,
        "css": css,
    }


@router.post("/website/export")
async def export_website(
    body: WebsiteExportRequest,
    current_user: dict = Depends(get_current_user),
):
    check_plan("pro", current_user["plan"])

    supabase = get_supabase_admin()
    result = (
        supabase.table("projects")
        .select("content")
        .eq("id", body.project_id)
        .eq("user_id", current_user["user_id"])
        .single()
        .execute()
    )

    if not result.data:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Project not found")

    content = result.data["content"]
    zip_bytes = build_website_zip(content)

    business_name = content.get("business_name", "website").lower().replace(" ", "-")
    filename = f"{business_name}-website.zip"

    supabase.table("exports").insert({
        "user_id": current_user["user_id"],
        "project_id": body.project_id,
        "export_type": "zip",
        "export_url": None,
    }).execute()

    return StreamingResponse(
        iter([zip_bytes]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Social ───────────────────────────────────────────────────────────────────


@router.post("/social/generate")
async def generate_social_post(
    body: SocialPostRequest,
    current_user: dict = Depends(get_current_user),
):
    check_plan("pro", current_user["plan"])
    post_text = await openai_service.generate_social_post(
        body.platform, body.topic, body.tone, body.business_name
    )
    return {"platform": body.platform, "post": post_text}


# ── Hashtags ─────────────────────────────────────────────────────────────────


@router.post("/hashtag/generate")
async def generate_hashtags(
    body: HashtagRequest,
    current_user: dict = Depends(get_current_user),
):
    check_plan("pro", current_user["plan"])
    tags = await openai_service.generate_hashtags(body.niche, body.topic, body.count)
    return {"hashtags": tags, "count": len(tags)}


# ── Branding ─────────────────────────────────────────────────────────────────


@router.post("/branding/generate")
async def generate_branding(
    body: BrandingRequest,
    current_user: dict = Depends(get_current_user),
):
    check_plan("pro", current_user["plan"])
    branding = await openai_service.generate_branding(body.category, body.keywords, body.vibe)
    return branding


# ── HTML/CSS helpers ─────────────────────────────────────────────────────────


def _resolve_primary(color_scheme: str) -> str:
    scheme_map = {
        "dark gold": "#c9a84c",
        "blue": "#3b82f6",
        "green": "#22c55e",
        "red": "#ef4444",
        "purple": "#a855f7",
        "orange": "#f97316",
    }
    lower = color_scheme.lower()
    for k, v in scheme_map.items():
        if k in lower:
            return v
    return "#c9a84c"


def _build_css(color_scheme: str) -> str:
    color = _resolve_primary(color_scheme)
    return f"""*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0d0d0d; color: #f5f0e8; line-height: 1.6; }}
header {{ background: #111; border-bottom: 2px solid {color}; padding: 1.5rem 2rem; }}
header h1 {{ color: {color}; font-size: 1.8rem; letter-spacing: 1px; }}
.hero {{ text-align: center; padding: 6rem 2rem; background: linear-gradient(135deg, #111 0%, #1a1a1a 100%); }}
.hero h2 {{ font-size: 3rem; color: {color}; margin-bottom: 1rem; }}
.hero p {{ font-size: 1.2rem; max-width: 600px; margin: 0 auto 2rem; }}
.btn {{ display: inline-block; padding: 0.8rem 2rem; background: {color}; color: #0d0d0d; font-weight: 700; border-radius: 4px; }}
section {{ padding: 4rem 2rem; max-width: 900px; margin: 0 auto; }}
footer {{ text-align: center; padding: 2rem; background: #111; color: #666; font-size: 0.85rem; }}"""


def _build_html(body: WebsiteGenerateRequest, watermark: bool) -> str:
    color = _resolve_primary(body.color_scheme)
    watermark_comment = "<!-- WATERMARK: Upgrade to Pro to remove this watermark -->" if watermark else ""
    watermark_banner = (
        '<div style="background:#c9a84c;color:#0d0d0d;text-align:center;padding:0.5rem;font-size:0.8rem;font-weight:700;">'
        "Preview only — <a href='/pricing' style='color:#0d0d0d;'>Upgrade to Pro</a> to export this site"
        "</div>"
        if watermark
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{body.business_name}</title>
  <link rel="stylesheet" href="style.css" />
  {watermark_comment}
</head>
<body>
  {watermark_banner}
  <header>
    <h1>{body.business_name}</h1>
    <nav>
      <a href="#about" style="color:{color};margin-left:1.5rem;">About</a>
      <a href="#services" style="color:{color};margin-left:1.5rem;">Services</a>
      <a href="#contact" style="color:{color};margin-left:1.5rem;">Contact</a>
    </nav>
  </header>

  <section class="hero">
    <h2>{body.tagline or body.business_name}</h2>
    <p>Your trusted {body.business_type} serving the community with excellence and care.</p>
    <a href="#contact" class="btn">Get Started</a>
  </section>

  <section id="about">
    <h2 style="color:{color};margin-bottom:1rem;">About Us</h2>
    <p>
      Welcome to {body.business_name}. We are a dedicated {body.business_type} committed
      to delivering exceptional value and memorable experiences for every customer we serve.
    </p>
  </section>

  <section id="services" style="background:#111;padding:4rem 2rem;">
    <div style="max-width:900px;margin:0 auto;">
      <h2 style="color:{color};margin-bottom:2rem;">Our Services</h2>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1.5rem;">
        <div style="background:#1a1a1a;padding:1.5rem;border-radius:8px;border-top:3px solid {color};">
          <h3 style="color:{color};margin-bottom:0.5rem;">Service One</h3>
          <p>Describe your first key offering and the value it brings to your clients.</p>
        </div>
        <div style="background:#1a1a1a;padding:1.5rem;border-radius:8px;border-top:3px solid {color};">
          <h3 style="color:{color};margin-bottom:0.5rem;">Service Two</h3>
          <p>Highlight another core service that makes your business stand out.</p>
        </div>
        <div style="background:#1a1a1a;padding:1.5rem;border-radius:8px;border-top:3px solid {color};">
          <h3 style="color:{color};margin-bottom:0.5rem;">Service Three</h3>
          <p>Showcase the third service that completes your full offering.</p>
        </div>
      </div>
    </div>
  </section>

  <section id="contact">
    <h2 style="color:{color};margin-bottom:1rem;">Contact Us</h2>
    <p>Ready to get started? Reach out and we'll get back to you promptly.</p>
    <form style="margin-top:1.5rem;display:flex;flex-direction:column;gap:1rem;max-width:480px;">
      <input type="text" placeholder="Your Name" style="padding:0.75rem;background:#1a1a1a;border:1px solid #333;color:#f5f0e8;border-radius:4px;" />
      <input type="email" placeholder="Email Address" style="padding:0.75rem;background:#1a1a1a;border:1px solid #333;color:#f5f0e8;border-radius:4px;" />
      <textarea placeholder="Your Message" rows="4" style="padding:0.75rem;background:#1a1a1a;border:1px solid #333;color:#f5f0e8;border-radius:4px;resize:vertical;"></textarea>
      <button type="submit" class="btn" style="cursor:pointer;border:none;">Send Message</button>
    </form>
  </section>

  <footer>
    <p>&copy; {body.business_name}. Built with <a href="https://brandfactorynola.com" style="color:{color};">The Brand Factory NOLA</a>.</p>
  </footer>
</body>
</html>"""
