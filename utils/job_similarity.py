"""
Job Similarity — data structure and embedding text generator for vector DB.

Creates structured text from job data for embedding generation. Extracts
skills, tech stack, salary, and experience from job descriptions.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.schemas import Job

# Common tech stack and skill keywords for extraction
TECH_KEYWORDS = [
    "python", "javascript", "typescript", "java", "go", "rust", "c++", "c#",
    "react", "vue", "angular", "node", "django", "flask", "fastapi",
    "tensorflow", "pytorch", "keras", "scikit-learn", "pandas", "numpy",
    "postgresql", "mysql", "mongodb", "redis", "kafka", "elasticsearch",
    "docker", "kubernetes", "aws", "gcp", "azure", "terraform",
    "machine learning", "deep learning", "nlp", "computer vision", "llm",
    "rest", "graphql", "grpc", "microservices", "ci/cd", "git",
]

# Experience level patterns
EXPERIENCE_PATTERNS = [
    r"(\d+)\+?\s*years?\s*(?:of\s*)?experience",
    r"(\d+)\s*-\s*(\d+)\s*years?\s*(?:of\s*)?experience",
    r"entry\s*level|junior|mid\s*level|senior|lead|principal|staff",
    r"(\d+)\s*years?\s*in\s*(?:software|engineering|development)",
]


def _extract_skills(description: str) -> list[str]:
    """Extract skills and tech stack from job description."""
    if not description:
        return []
    text = description.lower()
    found: set[str] = set()
    for kw in TECH_KEYWORDS:
        if kw in text:
            found.add(kw)
    # Also look for common patterns like "Python, Java, React"
    for match in re.finditer(r"\b([A-Za-z][A-Za-z0-9+#\.\-]*)\b", description):
        word = match.group(1).lower()
        if len(word) >= 3 and word in TECH_KEYWORDS:
            found.add(word)
    return sorted(found)


def _extract_salary(description: str) -> str:
    """Extract salary range from job description if present."""
    if not description:
        return ""
    # Patterns: $100k-$150k, $100,000 - $150,000, 10L-15L, etc.
    patterns = [
        r"\$[\d,]+(?:k|K)\s*[-–—]\s*\$[\d,]+(?:k|K)",
        r"\$[\d,]+(?:\.\d+)?\s*[-–—]\s*\$[\d,]+(?:\.\d+)?",
        r"[\d,]+(?:L|lakhs?)\s*[-–—]\s*[\d,]+(?:L|lakhs?)",
        r"[\d,]+(?:k|K)\s*[-–—]\s*[\d,]+(?:k|K)",
    ]
    for pat in patterns:
        m = re.search(pat, description, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return ""


def _normalize_experience(description: str) -> str:
    """Normalize experience requirements from job description."""
    if not description:
        return ""
    text = description.lower()
    # Try to extract years
    m = re.search(r"(\d+)\+?\s*years?\s*(?:of\s*)?(?:experience|exp)", text)
    if m:
        return f"{m.group(1)} years"
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*years?", text)
    if m:
        return f"{m.group(1)}-{m.group(2)} years"
    for level in ["entry level", "junior", "mid level", "senior", "lead", "principal"]:
        if level in text:
            return level.replace(" ", "_")
    return ""


def _extract_industry(company_industry: str, company_description: str) -> str:
    """Extract industry/category from company info."""
    parts = []
    if company_industry:
        parts.append(company_industry.strip())
    if company_description and len(company_description) < 200:
        parts.append(company_description.strip()[:100])
    return " ".join(parts) if parts else ""


class JobSimilarity:
    """
    Wraps job data with similarity metadata and generates embedding text.

    Creates a comprehensive text representation of a job for embedding
    generation, combining title, company, location, skills, tech stack,
    experience, and description.
    """

    def __init__(self, job: "Job") -> None:
        self.job = job
        self._skills: list[str] | None = None
        self._tech_stack: list[str] | None = None
        self._salary: str = ""
        self._experience: str = ""
        self._industry: str = ""

    def _extract_metadata(self) -> None:
        """Lazily extract metadata from job description."""
        if self._skills is not None:
            return
        desc = self.job.description or self.job.description_preview or ""
        self._skills = _extract_skills(desc)
        self._tech_stack = self._skills  # Same extraction for tech
        self._salary = _extract_salary(desc) or (self.job.salary or "")
        self._experience = _normalize_experience(desc) or (
            self.job.experience_required or ""
        )
        self._industry = _extract_industry(
            getattr(self.job, "company_industry", "") or "",
            getattr(self.job, "company_description", "") or "",
        )

    def _create_embedding_text(self) -> str:
        """
        Create comprehensive text for embedding generation.

        Combines job title, company, location, experience, skills, tech stack,
        industry, and description. Handles missing fields gracefully.
        """
        self._extract_metadata()
        parts: list[str] = []

        if self.job.title:
            parts.append(f"Title: {self.job.title}")
        if self.job.company:
            parts.append(f"Company: {self.job.company}")
        if self.job.location:
            parts.append(f"Location: {self.job.location}")
        if self._experience:
            parts.append(f"Experience: {self._experience}")
        if self._industry:
            parts.append(f"Industry: {self._industry}")
        if self.job.keywords_matched:
            parts.append(f"Keywords: {self.job.keywords_matched}")
        if self._skills:
            parts.append(f"Skills: {', '.join(self._skills)}")
        if self._salary:
            parts.append(f"Salary: {self._salary}")

        desc = self.job.description or self.job.description_preview or ""
        if desc:
            # Use first 1500 chars to avoid overly long embeddings
            parts.append(f"Description: {desc[:1500]}")

        return "\n".join(p for p in parts if p)

    @property
    def embedding_text(self) -> str:
        """Get the text used for embedding generation."""
        return self._create_embedding_text()

    @property
    def skills(self) -> list[str]:
        """Extracted skills from job description."""
        self._extract_metadata()
        return self._skills or []

    @property
    def tech_stack(self) -> list[str]:
        """Extracted tech stack keywords."""
        self._extract_metadata()
        return self._tech_stack or []

    @property
    def salary(self) -> str:
        """Extracted or provided salary range."""
        self._extract_metadata()
        return self._salary

    @property
    def experience(self) -> str:
        """Normalized experience requirement."""
        self._extract_metadata()
        return self._experience

    @property
    def industry(self) -> str:
        """Extracted industry/category."""
        self._extract_metadata()
        return self._industry
