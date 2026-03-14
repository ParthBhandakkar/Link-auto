"""
User profile — all personal details the bot needs when filling applications.
"""
from __future__ import annotations

PROFILE = {
    # ── Personal ────────────────────────────────────────────────────────
    "first_name": "Parth",
    "last_name": "Bhandakkar",
    "full_name": "Parth Bhandakkar",
    "email": "bhandakkarparth@gmail.com",
    "phone": "",  # Fill in your phone number
    "phone_country_code": "+91",
    "location": "Bilaspur, Chhattisgarh, India",
    "city": "Bilaspur",
    "state": "Chhattisgarh",
    "country": "India",
    "zip_code": "495001",
    "linkedin_url": "https://linkedin.com/in/parthbhandakkar",
    "github_url": "https://github.com/parthbhandakkar",
    "portfolio_url": "https://portfolio-bhandakkarparth.netlify.app/",

    # ── Professional ────────────────────────────────────────────────────
    "current_title": "Backend Developer & AI Engineer",
    "current_company": "SKDIV",
    "years_of_experience": "4",
    "total_experience_years": 4,

    # ── Compensation ────────────────────────────────────────────────────
    "current_ctc": "900000",
    "current_ctc_formatted": "9,00,000 INR per annum",
    "expected_ctc": "1100000",
    "expected_ctc_formatted": "11,00,000 INR per annum",
    "current_ctc_usd": "10800",   # Approximate
    "expected_ctc_usd": "13200",  # Approximate
    "notice_period": "30 days",
    "notice_period_weeks": "4",
    "willing_to_relocate": False,
    "work_authorization": "Indian Citizen",

    # ── Education ───────────────────────────────────────────────────────
    "degree": "Bachelor of Technology (B.Tech)",
    "field_of_study": "Electronics and Communication Engineering",
    "university": "IIIT Naya Raipur",
    "graduation_year": "2025",
    "gpa": "",

    # ── Skills ──────────────────────────────────────────────────────────
    "skills": [
        "Python", "JavaScript", "SQL", "FastAPI", "Django", "Flask",
        "Node.js", "TensorFlow", "Keras", "Pandas", "NumPy",
        "PostgreSQL", "MySQL", "MongoDB", "InfluxDB",
        "Kafka", "Docker", "Kubernetes", "Azure", "GCP",
        "LLM", "RAG", "LangChain", "Computer Vision", "LSTM",
        "Deep Learning", "Playwright", "Selenium", "React",
        "Power BI", "Grafana", "Git", "Linux",
    ],
    "primary_skills": [
        "Python", "FastAPI", "TensorFlow", "Deep Learning",
        "LLM", "RAG", "PostgreSQL", "Kafka", "Docker",
    ],

    # ── Target Job Configuration ────────────────────────────────────────
    "target_roles": [
        "AI Engineer",
        "ML Engineer",
        "Machine Learning Engineer",
        "AI/ML Engineer",
        "Data Scientist",
        "Backend Engineer",
        "Backend Developer",
        "Python Developer",
        "Python Backend Developer",
        "AI Developer",
        "Deep Learning Engineer",
        "MLOps Engineer",
        "NLP Engineer",
        "Data Engineer",
    ],
    "job_search_keywords": [
        "AI ML Engineer",
        "Machine Learning Engineer",
        "Data Scientist",
        "Backend Engineer Python",
        "AI Engineer",
        "Deep Learning Engineer",
        "Python Developer AI",
        "MLOps Engineer",
    ],
    "preferred_locations": ["Remote"],
    "job_type": "Remote",

    # ── Summary for LLM context ────────────────────────────────────────
    "professional_summary": (
        "Backend Developer & AI Engineer with 4+ years of experience building "
        "high-performance distributed systems, real-time data pipelines, and "
        "AI-powered solutions. Currently at SKDIV building enterprise-grade "
        "backend systems and microservices. Previously interned as AI Engineer "
        "at Devnullx designing deep learning pipelines with 89% stock prediction "
        "accuracy. Founded WorkZera, a B2B SaaS platform scaled to 300+ users. "
        "Expertise in Python, FastAPI, TensorFlow, Kafka, Docker, and LLM/RAG systems. "
        "B.Tech from IIIT Naya Raipur. Finalist in Shark Tank India. "
        "IEEE research paper under review in Ophthalmic AI."
    ),

    # ── Work Experience Detail ──────────────────────────────────────────
    "experience": [
        {
            "title": "Backend Developer",
            "company": "SKDIV",
            "start_date": "Sep 2024",
            "end_date": "Present",
            "location": "Remote",
            "description": (
                "Building enterprise-grade backend systems and microservices. "
                "Implemented MFA (JWT + OTP) with OAuth 2.0. Architected push "
                "notification microservice for 1000+ monthly notifications via Firebase. "
                "Built bidirectional calendar sync with Google/Apple Calendar. "
                "Architected Kafka-based event system processing 5K+ messages/day "
                "with 90%+ code coverage."
            ),
        },
        {
            "title": "AI Engineer Intern",
            "company": "Devnullx",
            "start_date": "Jan 2024",
            "end_date": "Jul 2024",
            "location": "Remote",
            "description": (
                "Designed deep learning pipeline using LSTM networks with 89% stock "
                "prediction accuracy. Built real-time market data ingestion from 4 "
                "exchanges using Python, Kafka, and InfluxDB. Developed Grafana "
                "dashboards for live visualization. Deployed models on Digital Ocean "
                "with fault-tolerance and checkpoint logic."
            ),
        },
        {
            "title": "Former CEO & Founder",
            "company": "WorkZera",
            "start_date": "May 2021",
            "end_date": "Oct 2023",
            "location": "Remote",
            "description": (
                "Founded and scaled B2B SaaS platform for remote team management. "
                "Led development of 10+ production-grade applications using React, "
                "Node.js, and Kafka. Automated CI/CD pipelines with Docker and "
                "GitHub Actions. Scaled to 300+ active users with ₹1L+ revenue."
            ),
        },
    ],

    # ── Common Application Answers ──────────────────────────────────────
    "common_answers": {
        "gender": "Male",
        "ethnicity": "Prefer not to say",
        "veteran_status": "No",
        "disability_status": "No",
        "sponsorship_required": "No",
        "legally_authorized": "Yes, authorized to work in India",
        "heard_about": "LinkedIn",
        "start_date": "Flexible / 30 days notice",
        "languages": ["English (Fluent)", "Hindi (Native)"],
        "certifications": [],
        "salary_expectation": "11,00,000 INR per annum",
        "willing_to_travel": "Yes, occasionally",
    },
}
