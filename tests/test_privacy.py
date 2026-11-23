import pytest
from jobradar.privacy import extraction_warnings, redact

# A made-up resume in the usual Indian fresher layout.
RESUME = """Rahul Kumar Sharma
+91 98765 43210 | rahul.sharma2026@gmail.com | linkedin.com/in/rahul-sharma | github.com/rahulsharma
H.No 12, Sector 21, Rohini, New Delhi - 110085

EDUCATION
B.Tech in Computer Science and Engineering, XYZ Institute of Technology   2022 - 2026
CGPA: 8.52/10
Class XII (CBSE) 2022: 92.4%

SKILLS
Languages: Python, Java, C++, SQL
Frameworks: FastAPI, React, Node.js, ASP.NET, socket.io, Pandas

PROJECTS
Multi-agent CI tool | Python, LangGraph - https://github.com/rahulsharma/resurge
- Cut review time by 40% across 1,200 PRs; call 987-654-3210 for a demo
Portfolio: rahul.vercel.app

PERSONAL DETAILS
Date of Birth: 14/03/2004
Father's Name: Suresh Sharma
Gender: Male
Address: Rohini, Delhi
Languages Known: English, Hindi
Aadhaar: 1234 5678 9012

DECLARATION
I hereby declare that the above information is true to the best of my knowledge.
Place: New Delhi
Date:
(Rahul Kumar Sharma)
"""


def test_redact_removes_personal_details_and_keeps_what_matching_needs():
    r = redact(RESUME, {"name": ""})
    for secret in ("Rahul", "98765", "43210", "rahul.sharma2026", "rahul-sharma", "rahulsharma", "110085",
                   "Rohini", "14/03/2004", "Suresh", "1234 5678", "987-654-3210", "rahul.vercel.app", "Male",
                   "hereby", "Hindi"):
        assert secret not in r.text, secret
    for kept in ("2022 - 2026", "CGPA: 8.52/10", "92.4%", "Python, Java, C++, SQL", "Node.js", "ASP.NET",
                 "socket.io", "Pandas", "40% across 1,200 PRs", "Multi-agent CI tool", "XYZ Institute",
                 "EDUCATION", "PROJECTS"):
        assert kept in r.text, kept
    assert r.text.startswith("Contact: [EMAIL] | [LINKEDIN] | [GITHUB] | [PHONE]\nEDUCATION")
    assert "[GITHUB-2]" in r.text and "[PHONE-2]" in r.text and "Portfolio: [LINK]" in r.text
    assert any("header block" in x for x in r.removed) and any("personal-detail" in x for x in r.removed)
    assert all("98765" not in x and "gmail" not in x for x in r.removed)  # the summary never shows values


def test_restore_puts_real_values_back_locally():
    r = redact(RESUME, {})
    draft = "Hi, I'm [NAME]. [PHONE] / [EMAIL]\n[GITHUB-2] [GITHUB] [UNKNOWN]"
    out = r.restore(draft)
    assert out == ("Hi, I'm Rahul Kumar Sharma. +91 98765 43210 / rahul.sharma2026@gmail.com\n"
                   "https://github.com/rahulsharma/resurge github.com/rahulsharma [UNKNOWN]")


def test_redact_without_headings_or_with_profile_name():
    text = "Priya Nair, priya@example.in, 9123456789. Built a RAG chatbot with FastAPI. Priya led the team."
    r = redact(text, {"name": "Priya Nair"})
    assert "priya@example.in" not in r.text and "9123456789" not in r.text and "Priya Nair" not in r.text
    assert "Built a RAG chatbot with FastAPI" in r.text
    plain = redact("Skills: Python 3.11, numbers 2019-2023, 10.5 LPA, ranked 1532 of 98000", {})
    assert plain.text == "Skills: Python 3.11, numbers 2019-2023, 10.5 LPA, ranked 1532 of 98000"
    intl = redact("Reach me: +1 (415) 555-0123 or 098765 43210.", {})
    assert "415" not in intl.text and "43210" not in intl.text and intl.text.endswith(".")


def test_extraction_warnings():
    assert extraction_warnings("") and "image-only" in extraction_warnings("Name\n")[0]
    good = " ".join(["Built REST APIs in Python with FastAPI and PostgreSQL for a college project."] * 10)
    assert extraction_warnings(good) == []
    merged = " ".join(["BuiltRESTAPIsinPythonwithFastAPIandPostgreSQL forcollegeprojectsandinternships"] * 10)
    assert "run together" in extraction_warnings(merged)[0]
    spaced = " ".join(list("Python developer with FastAPI experience " * 12))
    assert any("spaced out" in w for w in extraction_warnings(spaced))


def test_resume_found_under_any_name(tmp_path):
    from jobradar.config import DEFAULT_PROFILE, Settings, resume_text

    (tmp_path / "profile").mkdir()
    settings = Settings(root=tmp_path, profile=dict(DEFAULT_PROFILE), sources={})
    assert resume_text(settings)[1].startswith("profile.yaml summary")
    (tmp_path / "profile" / "Jane_Doe_Resume (1).txt").write_text("Jane Doe\nSkills: Python")
    (tmp_path / "profile" / "README.md").write_text("not a resume")
    assert resume_text(settings) == ("Jane Doe\nSkills: Python", "profile/Jane_Doe_Resume (1).txt")
    (tmp_path / "profile" / "resume.md").write_text("Configured name wins")
    assert resume_text(settings)[1] == "profile/resume.md"


@pytest.mark.parametrize("text,secret", [
    ("Priya Sharma | Software Developer\nEducation\nB.Tech", "Priya"),
    ("RESUME\nPriya Sharma\nSkills\nPython. Priya Sharma built X.", "Priya"),
    ("José Fernández García\nExperience\nWorked with José Fernández García on Y", "José"),
    ("Skills\nmail me: priya at gmail dot com", "priya"),
    ("Skills\npriya[at]gmail[dot]com", "priya"),
    ("Skills\npriya @ gmail.com", "priya"),
    ("Skills\npriya＠gmail.com", "priya"),
    ("Skills\npriya\u200b@gmail.com", "priya"),
    ("Skills\ncall 98 7654 3210", "7654"),
    ("Skills\ncall (98765) 43210", "43210"),
    ("Skills\ncall ९८७६५४३२१०", "9876"),
    ("Skills\ncall ९८७६५४३२१०", "९८७६"),
    ("Skills\ncall ９８７６５４３２１０", "9876"),
    ("Skills\noffice 011-2345 6789", "6789"),
    ("Skills\n(415) 555-0100", "0100"),
    ("Skills\nid 234567890123", "234567890123"),
    ("Skills\nid 2345.6789.0123", "6789"),
    ("Skills\nPAN ABCDE1234F", "ABCDE"),
    ("Skills\nRohini New Delhi 110085", "110085"),
    ("Skills\nwas born on 14 August 2003", "2003"),
    ("Skills\nfind me @priya_codes", "priya_codes"),
    ("Skills\nportfolio priyasharma.dev", "priyasharma"),
    ("Priya Sharma\nH.No 12, Sector 7, Rohini, Delhi\nACADEMIC QUALIFICATION\nB.Tech", "Sector 7"),
    ("Skills\nLinkedIn: priya-sharma-123 | GitHub: priyasharma", "priya-sharma-123"),
    ("Skills\nLinkedIn: priya-sharma-123 | GitHub: priyasharma", "priyasharma"),
    ("Priya Sharma\nSkills\nBuilt by P. Sharma and Priya S.", "P. Sharma"),
    ("Priya Sharma\nSkills\nBuilt by P. Sharma and Priya S.", "Priya S."),
])
def test_redaction_catches_odd_formats(text, secret):
    assert secret not in redact(text, {}).text


def test_redaction_leaves_ordinary_resume_text_alone():
    text = ("Experience\nBuilt REST APIs with Node.js, Next.js and Socket.io; deployed on AWS EC2.\n"
            "Solved 450+ problems. CGPA: 8.7 / 10, 2022 – 2026. Percentage: 87.8%\n"
            "Interned at Amazon.com, worked on ASP.NET; used @nestjs/core and React 18.2.0. 6-9 LPA, 2 years.")
    assert redact(text, {}).text == text
