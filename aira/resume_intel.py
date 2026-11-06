"""Resume intelligence for Careers: ATS readiness, missing keywords, version changes, skill gaps, certifications.

Everything here is deterministic and explainable: each score lists the checks behind it and the fix for each
one that fails. Skills come from the same lexicon the job scorer uses (:mod:`aira.scoring`), and the resume
structure from the same parser that lays out the PDF employers receive (:mod:`aira.resume_render`), so the
advice and the uploaded file agree.

Nothing here invents experience: missing keywords that the master resume already supports are offered to bring
back; the rest are named as gaps to close (learning, a project, a certification), never as words to add.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from aira import resume_render
from aira.scoring import (
    candidate_skill_set,
    find_skills,
    job_skill_requirements,
    skill_evidenced,
    skill_label,
)

__all__ = [
    "CERTIFICATIONS",
    "ats_readiness",
    "certification_suggestions",
    "change_summary",
    "missing_keywords",
    "skill_gaps",
]

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_LINK_RE = re.compile(r"linkedin\.com/|github\.com/|https?://|www\.", re.IGNORECASE)
_NUMBER_RE = re.compile(
    r"(?<![\w.])(?:[$\u20ac\u00a3\u20b9]\s?)?\d[\d,.]*\s?(?:%|k|m|bn|x|\+)?(?![\w])",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_FIRST_PERSON_RE = re.compile(r"\b(I|me|my|myself)\b")
_TABLE_LINE_RE = re.compile(r"(?:\|[^|]*){3,}\||\t.*\t")
_CORE_SECTIONS = (("experience", 8), ("education", 6), ("skills", 6))


def _check(
    check_id: str, label: str, points: float, maximum: float, detail: str, fix: str = ""
) -> dict[str, Any]:
    passed = points >= maximum
    return {
        "id": check_id,
        "label": label,
        "points": round(points, 1),
        "max": maximum,
        "passed": passed,
        "detail": detail,
        "fix": "" if passed else fix,
    }


def _grade(score: int) -> str:
    if score >= 85:
        return "Ready"
    if score >= 70:
        return "Almost there"
    if score >= 50:
        return "Needs work"
    return "Not ready"


def _blocks(doc: resume_render.ResumeDoc, kind: str) -> list[resume_render.Block]:
    return [
        block
        for section in doc.sections
        for block in section.blocks
        if block.kind == kind
    ]


def ats_readiness(
    text: str,
    *,
    job: Mapping[str, Any] | None = None,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """How well an applicant tracking system will read this resume, from 0 to 100, with the fixes.

    Checks: parseable structure (sections, bullets, dated roles, no table-like lines, characters the PDF can
    show), standard headings (Experience, Education, Skills), contact details (name, email, phone, location,
    a profile link), keyword coverage for ``job`` (its required and preferred skills; left out of the total
    without a job) and length (words and pages).
    """
    text = str(text or "")
    profile = profile or {}
    doc = resume_render.parse_resume(text)
    layout = resume_render.layout_resume(text, title="ATS check")
    words = len(re.findall(r"\w+", text))
    bullets = _blocks(doc, "bullet")
    roles = _blocks(doc, "role")
    titled = [section for section in doc.sections if section.title]
    kinds = {section.kind for section in titled}
    checks: list[dict[str, Any]] = []

    # -- parseable structure (20) -------------------------------------------------------
    structure = 0.0
    notes: list[str] = []
    fixes: list[str] = []
    if len(titled) >= 3:
        structure += 6
    else:
        fixes.append(
            "Split the resume into sections with headings (Summary, Experience, Education, Skills)."
        )
    if len(bullets) >= 3:
        structure += 5
    else:
        fixes.append("List achievements as bullet points under each role.")
    dated = sum(1 for role in roles if role.right)
    if roles and dated >= max(1, len(roles) // 2):
        structure += 4
    else:
        fixes.append(
            "Give each role a line with its title, employer and dates (for example Jan 2021 - Present)."
        )
    table_lines = [line for line in text.splitlines() if _TABLE_LINE_RE.search(line)]
    if not table_lines:
        structure += 3
    else:
        fixes.append(
            "Replace table-like lines (several | or tab-separated columns) with plain lines; ATS parsers read tables out of order."
        )
    lost = [warning for warning in layout.warnings if "cannot show" in warning]
    if not lost:
        structure += 2
    else:
        fixes.append(
            'Replace characters the PDF fonts cannot show (they print as "?"), or send your Word resume.'
        )
    notes.append(
        f"{len(titled)} sections, {len(bullets)} bullets, {dated} of {len(roles)} roles dated."
    )
    checks.append(
        _check(
            "structure",
            "Parseable structure",
            structure,
            20,
            " ".join(notes),
            " ".join(fixes),
        )
    )

    # -- standard headings (20) ---------------------------------------------------------
    headings = 0.0
    missing = [kind for kind, _points in _CORE_SECTIONS if kind not in kinds]
    for kind, points in _CORE_SECTIONS:
        if kind in kinds:
            headings += points
    odd = [section.title for section in titled if section.kind == "other"]
    detail = (
        f"Found: {', '.join(section.title for section in titled) or 'no headings'}."
        + (f" Not standard: {', '.join(odd)}." if odd else "")
    )
    fix_parts = []
    if missing:
        fix_parts.append(
            f"Add standard headings: {', '.join(name.title() for name in missing)}."
        )
    if odd:
        fix_parts.append(
            "Rename unusual headings to standard ones (Experience, Projects, Skills) so ATS sorts them."
        )
    if odd and not missing:
        headings = max(0.0, headings - 2)
    checks.append(
        _check(
            "headings", "Standard headings", headings, 20, detail, " ".join(fix_parts)
        )
    )

    # -- contact details (15) ---------------------------------------------------------------
    header = " ".join([doc.name, doc.headline, *doc.contact])
    head_text = header + " " + text[:600]
    contact_points = 0.0
    contact_missing: list[str] = []
    if doc.name:
        contact_points += 3
    else:
        contact_missing.append("your name as the first line")
    if _EMAIL_RE.search(head_text):
        contact_points += 4
    else:
        contact_missing.append("an email address")
    if _PHONE_RE.search(head_text):
        contact_points += 4
    else:
        contact_missing.append("a phone number")
    location = str(profile.get("location") or profile.get("city") or "").strip()
    if (location and location.casefold() in head_text.casefold()) or any(
        "," in item and not _EMAIL_RE.search(item) and not _LINK_RE.search(item)
        for item in doc.contact
    ):
        contact_points += 2
    else:
        contact_missing.append("your city and country")
    if _LINK_RE.search(head_text):
        contact_points += 2
    else:
        contact_missing.append("a LinkedIn or portfolio link")
    checks.append(
        _check(
            "contact",
            "Contact details",
            contact_points,
            15,
            "Name, email, phone, location and a profile link at the top.",
            f"Add {', '.join(contact_missing)} at the top (not in a header or footer)."
            if contact_missing
            else "",
        )
    )

    # -- keyword coverage (30, only with a job) ------------------------------------------------
    keywords: dict[str, Any] | None = None
    if job is not None and (job.get("description") or job.get("required_skills")):
        keywords = missing_keywords(text, job, profile=profile)
        required_total = keywords["counts"]["required"]
        preferred_total = keywords["counts"]["preferred"]
        required_hit = required_total - len(keywords["required"])
        preferred_hit = preferred_total - len(keywords["preferred"])
        coverage = 0.0
        if required_total:
            coverage += 24 * required_hit / required_total
        else:
            coverage += 24
        if preferred_total:
            coverage += 6 * preferred_hit / preferred_total
        else:
            coverage += 6
        named = [item["label"] for item in keywords["required"][:6]]
        checks.append(
            _check(
                "keywords",
                "Keywords from this job",
                coverage,
                30,
                f"{required_hit} of {required_total} required and {preferred_hit} of {preferred_total} preferred skills appear.",
                (
                    f"Work in the required skills you really have: {', '.join(named)}."
                    if named
                    else "Add the preferred skills you really have."
                ),
            )
        )

    # -- length (15) -------------------------------------------------------------------------------
    pages = layout.page_count
    if 350 <= words <= 900 and pages <= 2:
        length = 15.0
    elif 250 <= words <= 1200 and pages <= 3:
        length = 9.0
    else:
        length = 4.0 if words else 0.0
    checks.append(
        _check(
            "length",
            "Length",
            length,
            15,
            f"{words} words, {pages} page{'s' if pages != 1 else ''} in the PDF layout.",
            "Aim for 350 to 900 words on one or two pages: cut older roles to their best results."
            if words > 900 or pages > 2
            else "Add more detail: results with numbers for each recent role (350 to 900 words reads best).",
        )
    )

    # -- writing (advice only, not scored) -----------------------------------------------------------
    advice: list[str] = []
    with_numbers = sum(1 for bullet in bullets if _NUMBER_RE.search(bullet.text))
    if bullets and with_numbers < len(bullets) / 3:
        advice.append(
            "Few bullets show a result with a number (time saved, %, revenue, users); add the ones you can back up."
        )
    if _FIRST_PERSON_RE.search(" ".join(block.text for block in bullets)):
        advice.append(
            'Drop "I" and "my" from bullets: start each with a verb (Led, Built, Cut).'
        )
    long_bullets = sum(1 for bullet in bullets if len(bullet.text) > 260)
    if long_bullets:
        advice.append(
            f"{long_bullets} bullet{'s are' if long_bullets != 1 else ' is'} longer than two lines; split or tighten them."
        )

    earned = sum(item["points"] for item in checks)
    available = sum(item["max"] for item in checks)
    score = round(100 * earned / available) if available else 0
    return {
        "score": score,
        "grade": _grade(score),
        "checks": checks,
        "fixes": [item["fix"] for item in checks if item["fix"]],
        "advice": advice,
        "keywords_scored": keywords is not None,
        "stats": {
            "words": words,
            "pages": pages,
            "sections": len(titled),
            "bullets": len(bullets),
            "roles": len(roles),
        },
    }


def _skill_entry(skill: str, importance: str, in_master: bool) -> dict[str, Any]:
    return {
        "skill": skill,
        "label": skill_label(skill),
        "importance": importance,
        "in_master": in_master,
        "advice": (
            "Your master resume or verified skills show this: bring it into this version."
            if in_master
            else "Not in your resume: add it only if it is true. Otherwise it is a gap to close."
        ),
    }


def missing_keywords(
    text: str,
    job: Mapping[str, Any],
    *,
    master_text: str = "",
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The job's required and preferred skills this resume does not mention, and whether the master has them.

    Title words of the posting that the resume never uses are listed as ``title_terms``.
    """
    profile = profile or {}
    required, preferred = job_skill_requirements(job)
    have = candidate_skill_set({}, text)
    verified = candidate_skill_set(
        {"verified_skills": profile.get("verified_skills") or []}, ""
    )
    master = candidate_skill_set({}, master_text) | verified

    def evidenced(skill: str, skills: set[str], source: str) -> bool:
        return skill_evidenced(skill, skills, source)

    missing_required = [
        _skill_entry(skill, "required", evidenced(skill, master, master_text))
        for skill in required
        if not evidenced(skill, have, text)
    ]
    missing_preferred = [
        _skill_entry(skill, "preferred", evidenced(skill, master, master_text))
        for skill in preferred
        if not evidenced(skill, have, text)
    ]
    lowered = text.casefold()
    stop = {
        "and",
        "the",
        "for",
        "with",
        "senior",
        "junior",
        "lead",
        "staff",
        "principal",
        "remote",
        "hybrid",
        "of",
        "to",
        "in",
        "ii",
        "iii",
        "sr",
        "jr",
    }
    title_terms = [
        word
        for word in dict.fromkeys(
            re.findall(r"[A-Za-z][A-Za-z+#.]{1,30}", str(job.get("title") or ""))
        )
        if word.casefold() not in stop and word.casefold() not in lowered
    ][:6]
    return {
        "required": missing_required,
        "preferred": missing_preferred,
        "title_terms": title_terms,
        "counts": {"required": len(required), "preferred": len(preferred)},
        "matched": [
            skill_label(skill)
            for skill in [*required, *preferred]
            if evidenced(skill, have, text)
        ],
    }


def _lines(text: str) -> list[str]:
    out = []
    for line in str(text or "").splitlines():
        cleaned = re.sub(r"^[\s#*\-\u2022+>]+|\*\*|__", "", line).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if cleaned:
            out.append(cleaned)
    return out


def _figures(text: str) -> set[str]:
    found = set()
    for match in _NUMBER_RE.finditer(str(text or "")):
        token = re.sub(r"\s+", "", match.group(0)).rstrip(".,").casefold()
        if (
            token
            and not _YEAR_RE.fullmatch(token)
            and len(token.strip("0123456789")) < len(token)
        ):
            found.add(token)
    return found


def change_summary(master_text: str, tailored_text: str) -> dict[str, Any]:
    """What a tailored version changed against the master resume: sections, lines, skills and figures.

    ``new_skills`` and ``new_figures`` are claims the master resume does not contain; the editor asks the person
    to check each one before approving (Monosage never adds experience on its own).
    """
    master_doc = resume_render.parse_resume(master_text)
    tailored_doc = resume_render.parse_resume(tailored_text)
    master_titles = [s.title for s in master_doc.sections if s.title]
    tailored_titles = [s.title for s in tailored_doc.sections if s.title]
    master_keys = {title.casefold() for title in master_titles}
    tailored_keys = {title.casefold() for title in tailored_titles}
    common_master = [t for t in master_titles if t.casefold() in tailored_keys]
    common_tailored = [t for t in tailored_titles if t.casefold() in master_keys]
    old_lines, new_lines = _lines(master_text), _lines(tailored_text)
    matcher = difflib.SequenceMatcher(
        a=[line.casefold() for line in old_lines],
        b=[line.casefold() for line in new_lines],
        autojunk=False,
    )
    added: list[str] = []
    removed: list[str] = []
    reworded = 0
    for tag, a1, a2, b1, b2 in matcher.get_opcodes():
        if tag == "insert":
            added.extend(new_lines[b1:b2])
        elif tag == "delete":
            removed.extend(old_lines[a1:a2])
        elif tag == "replace":
            reworded += min(a2 - a1, b2 - b1)
            if b2 - b1 > a2 - a1:
                added.extend(new_lines[b1 + (a2 - a1) : b2])
            elif a2 - a1 > b2 - b1:
                removed.extend(old_lines[a1 + (b2 - b1) : a2])
    master_skills = set(find_skills(master_text))
    tailored_skills = set(find_skills(tailored_text))
    new_figures = sorted(_figures(tailored_text) - _figures(master_text))
    master_words = len(re.findall(r"\w+", master_text or ""))
    tailored_words = len(re.findall(r"\w+", tailored_text or ""))
    kept = sum(block.size for block in matcher.get_matching_blocks())
    return {
        "sections": {
            "added": [t for t in tailored_titles if t.casefold() not in master_keys],
            "removed": [t for t in master_titles if t.casefold() not in tailored_keys],
            "reordered": common_master != common_tailored,
        },
        "lines": {
            "kept": kept,
            "reworded": reworded,
            "added": len(added),
            "removed": len(removed),
            "added_examples": added[:6],
            "removed_examples": removed[:6],
        },
        "skills": {
            "new": [
                skill_label(s)
                for s in find_skills(tailored_text)
                if s not in master_skills
            ],
            "dropped": [
                skill_label(s)
                for s in find_skills(master_text)
                if s not in tailored_skills
            ],
        },
        "new_figures": new_figures[:12],
        "words": {"master": master_words, "tailored": tailored_words},
        "similarity": round(matcher.ratio(), 2),
        "has_master": bool(str(master_text or "").strip()),
    }


# --------------------------------------------------------------------------- #
# Skill gaps across saved jobs and certifications
# --------------------------------------------------------------------------- #
CERTIFICATIONS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "aws": (
        ("AWS Certified Cloud Practitioner", "Amazon Web Services", "foundational"),
        (
            "AWS Certified Solutions Architect - Associate",
            "Amazon Web Services",
            "associate",
        ),
    ),
    "azure": (
        (
            "Microsoft Certified: Azure Fundamentals (AZ-900)",
            "Microsoft",
            "foundational",
        ),
        (
            "Microsoft Certified: Azure Administrator Associate (AZ-104)",
            "Microsoft",
            "associate",
        ),
        (
            "Microsoft Certified: Azure Solutions Architect Expert (AZ-305)",
            "Microsoft",
            "expert",
        ),
    ),
    "gcp": (
        ("Google Cloud Associate Cloud Engineer", "Google Cloud", "associate"),
        ("Google Cloud Professional Cloud Architect", "Google Cloud", "professional"),
    ),
    "kubernetes": (
        (
            "Certified Kubernetes Administrator (CKA)",
            "The Linux Foundation / CNCF",
            "professional",
        ),
        (
            "Certified Kubernetes Application Developer (CKAD)",
            "The Linux Foundation / CNCF",
            "professional",
        ),
    ),
    "terraform": (
        ("HashiCorp Certified: Terraform Associate", "HashiCorp", "associate"),
    ),
    "infrastructure_as_code": (
        ("HashiCorp Certified: Terraform Associate", "HashiCorp", "associate"),
    ),
    "docker": (("Docker Certified Associate", "Mirantis", "associate"),),
    "linux": (
        (
            "Linux Foundation Certified System Administrator (LFCS)",
            "The Linux Foundation",
            "associate",
        ),
        ("Red Hat Certified System Administrator (RHCSA)", "Red Hat", "associate"),
    ),
    "openshift": (
        (
            "Red Hat Certified Specialist in OpenShift Administration",
            "Red Hat",
            "specialist",
        ),
    ),
    "networking": (("Cisco Certified Network Associate (CCNA)", "Cisco", "associate"),),
    "cloud_security": (
        (
            "Certificate of Cloud Security Knowledge (CCSK)",
            "Cloud Security Alliance",
            "professional",
        ),
    ),
    "security_architecture": (("CISSP", "ISC2", "professional"),),
    "network_security": (("CompTIA Security+", "CompTIA", "foundational"),),
    "appsec": (
        ("GIAC Web Application Penetration Tester (GWAPT)", "GIAC", "professional"),
    ),
    "risk_management": (
        (
            "Certified in Risk and Information Systems Control (CRISC)",
            "ISACA",
            "professional",
        ),
    ),
    "privacy": (
        ("Certified Information Privacy Professional (CIPP)", "IAPP", "professional"),
    ),
    "project_management": (
        ("Certified Associate in Project Management (CAPM)", "PMI", "foundational"),
        ("Project Management Professional (PMP)", "PMI", "professional"),
    ),
    "pmp": (("Project Management Professional (PMP)", "PMI", "professional"),),
    "program_management": (
        ("Program Management Professional (PgMP)", "PMI", "professional"),
    ),
    "scrum": (
        ("Professional Scrum Master I (PSM I)", "Scrum.org", "foundational"),
        ("Certified ScrumMaster (CSM)", "Scrum Alliance", "foundational"),
    ),
    "agile": (("PMI Agile Certified Practitioner (PMI-ACP)", "PMI", "professional"),),
    "safe": (("SAFe Agilist", "Scaled Agile", "foundational"),),
    "itil": (("ITIL 4 Foundation", "PeopleCert / Axelos", "foundational"),),
    "product_management": (
        ("Professional Scrum Product Owner I (PSPO I)", "Scrum.org", "foundational"),
    ),
    "business_analysis": (
        ("Certified Business Analysis Professional (CBAP)", "IIBA", "professional"),
    ),
    "togaf": (
        ("TOGAF Enterprise Architecture Foundation", "The Open Group", "foundational"),
    ),
    "enterprise_architecture": (
        (
            "TOGAF Enterprise Architecture Practitioner",
            "The Open Group",
            "professional",
        ),
    ),
    "machine_learning": (
        (
            "AWS Certified Machine Learning Engineer - Associate",
            "Amazon Web Services",
            "associate",
        ),
        (
            "Google Cloud Professional Machine Learning Engineer",
            "Google Cloud",
            "professional",
        ),
    ),
    "data_engineering": (
        ("Google Cloud Professional Data Engineer", "Google Cloud", "professional"),
        (
            "Microsoft Certified: Fabric Data Engineer Associate",
            "Microsoft",
            "associate",
        ),
    ),
    "databricks": (
        ("Databricks Certified Data Engineer Associate", "Databricks", "associate"),
    ),
    "snowflake": (("SnowPro Core Certification", "Snowflake", "foundational"),),
    "spark": (
        (
            "Databricks Certified Associate Developer for Apache Spark",
            "Databricks",
            "associate",
        ),
    ),
    "kafka": (
        ("Confluent Certified Developer for Apache Kafka", "Confluent", "associate"),
    ),
    "power_bi": (
        (
            "Microsoft Certified: Power BI Data Analyst Associate (PL-300)",
            "Microsoft",
            "associate",
        ),
    ),
    "tableau": (
        ("Tableau Certified Data Analyst", "Tableau / Salesforce", "associate"),
    ),
    "sql": (("Oracle Database SQL Certified Associate", "Oracle", "associate"),),
    "salesforce": (("Salesforce Certified Administrator", "Salesforce", "associate"),),
    "servicenow": (
        ("ServiceNow Certified System Administrator", "ServiceNow", "associate"),
    ),
    "sap": (("SAP Certified Associate (in the module you use)", "SAP", "associate"),),
    "java": (
        ("Oracle Certified Professional: Java SE Developer", "Oracle", "professional"),
    ),
    "python": (
        (
            "PCAP: Certified Associate in Python Programming",
            "Python Institute",
            "associate",
        ),
    ),
    "devops": (
        (
            "AWS Certified DevOps Engineer - Professional",
            "Amazon Web Services",
            "professional",
        ),
    ),
    "sre": (
        (
            "Google Cloud Professional Cloud DevOps Engineer",
            "Google Cloud",
            "professional",
        ),
    ),
    "finops": (("FinOps Certified Practitioner", "FinOps Foundation", "foundational"),),
    "generative_ai": (
        ("AWS Certified AI Practitioner", "Amazon Web Services", "foundational"),
    ),
    "llm": (("AWS Certified AI Practitioner", "Amazon Web Services", "foundational"),),
}


def certification_suggestions(skills: Iterable[str]) -> list[dict[str, Any]]:
    """Curated certifications that evidence the given canonical skills (no duplicates, in skill order)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for skill in skills:
        for name, issuer, level in CERTIFICATIONS.get(skill, ()):
            if name in seen:
                for item in out:
                    if (
                        item["name"] == name
                        and skill_label(skill) not in item["skills"]
                    ):
                        item["skills"].append(skill_label(skill))
                continue
            seen.add(name)
            out.append(
                {
                    "name": name,
                    "issuer": issuer,
                    "level": level,
                    "skills": [skill_label(skill)],
                    "source": "curated",
                }
            )
    return out


def skill_gaps(
    jobs: Sequence[Mapping[str, Any]],
    *,
    resume_text: str,
    profile: Mapping[str, Any] | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """The skills most often missing across ``jobs``, and the jobs each one would unlock.

    A job is unlocked by a skill when that skill is the only required skill the person lacks for it.
    """
    profile = profile or {}
    have = candidate_skill_set(profile, resume_text)
    stats: dict[str, dict[str, Any]] = {}
    considered = 0
    complete = 0
    for job in jobs:
        required, preferred = job_skill_requirements(job)
        if not required and not preferred:
            continue
        considered += 1
        missing_required = [
            s for s in required if not skill_evidenced(s, have, resume_text)
        ]
        missing_preferred = [
            s for s in preferred if not skill_evidenced(s, have, resume_text)
        ]
        if not missing_required:
            complete += 1
        reference = {
            "job_id": job.get("job_id"),
            "title": job.get("title") or "",
            "company": job.get("company") or "",
        }
        for skill, importance in [
            *((s, "required") for s in missing_required),
            *((s, "preferred") for s in missing_preferred),
        ]:
            entry = stats.setdefault(
                skill,
                {
                    "skill": skill,
                    "label": skill_label(skill),
                    "required": 0,
                    "preferred": 0,
                    "jobs": [],
                    "unlocks": [],
                },
            )
            entry[importance] += 1
            if len(entry["jobs"]) < 12:
                entry["jobs"].append({**reference, "importance": importance})
            if importance == "required" and len(missing_required) == 1:
                entry["unlocks"].append(reference)
    ranked = sorted(
        stats.values(),
        key=lambda item: (
            -(len(item["unlocks"]) * 3 + item["required"] * 2 + item["preferred"]),
            item["label"],
        ),
    )[:limit]
    for item in ranked:
        item["certifications"] = certification_suggestions([item["skill"]])[:2]
    return {
        "skills": ranked,
        "jobs_considered": considered,
        "jobs_without_gaps": complete,
        "your_skills": sorted(skill_label(skill) for skill in have)[:60],
    }
