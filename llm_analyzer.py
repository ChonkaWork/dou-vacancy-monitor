import re


TECH_SKILLS = [
    "java", "kotlin", "scala", "groovy", "clojure", "jvm",
    "spring", "spring boot", "spring mvc", "spring data", "spring security", "spring cloud",
    "hibernate", "jpa", "mybatis",
    "kafka", "rabbitmq", "activemq", "jms", "redis", "memcached",
    "postgresql", "postgres", "mysql", "oracle", "mssql", "sql server", "mongodb", "cassandra",
    "docker", "kubernetes", "k8s", "jenkins", "gitlab ci", "github actions", "terraform", "ansible",
    "aws", "azure", "gcp", "google cloud", "lambda", "s3", "ec2",
    "rest", "rest api", "graphql", "grpc", "soap", "microservices",
    "junit", "testng", "mockito", "powermock", "cucumber", "selenium",
    "maven", "gradle", "ant", "ivy",
    "git", "svn", "mercurial",
    "agile", "scrum", "kanban", "jira", "confluence",
    "linux", "bash", "shell", "powershell",
    "react", "angular", "vue", "javascript", "typescript", "node", "nodejs",
    "python", "django", "flask", "fastapi", "numpy", "pandas",
    "go", "golang", "rust", "c++", "cpp", "c#", "dotnet",
    "elasticsearch", "solr", "lucene",
    "prometheus", "grafana", "datadog", "new relic",
    "ci/cd", "devops", "iac", "infrastructure as code",
    "oauth2", "jwt", "saml", "ldap",
    "html", "css", "sass", "scss", "webpack", "vite",
    "swagger", "openapi", "postman",
    "jmeter", "gatling", "loadrunner",
    "sonarqube", "checkstyle", "pmd",
    "intellij", "eclipse", "vscode",
    "java 8", "java 11", "java 17", "java 21",
]


def extract_skills_from_cv(cv_text: str) -> list[str]:
    low = cv_text.lower()
    found = []
    for skill in TECH_SKILLS:
        if skill in low:
            if skill == "java" and "javascript" in low:
                continue
            if skill in ("ci/cd",) or " " not in skill:
                if re.search(rf"\b{re.escape(skill)}\b", low):
                    found.append(skill)
            else:
                found.append(skill)
    return sorted(set(found))


def analyze_job_match(cv_text: str, job_title: str, job_description: str) -> tuple[int, list[str], str]:
    cv_skills = extract_skills_from_cv(cv_text)
    combined = f"{job_title} {job_description}".lower()

    matched = []
    all_found = set()
    for skill in TECH_SKILLS:
        if skill in combined and skill in cv_skills:
            matched.append(skill)
            all_found.add(skill)
        elif skill in combined:
            all_found.add(skill)

    if not cv_skills:
        score = 0
    else:
        score = int((len(matched) / max(len(cv_skills), 1)) * 100)
    score = min(score, 100)

    missing = [s for s in all_found if s not in matched]
    missing_str = ", ".join(missing[:5]) if missing else "none"
    summary = f"Matched {len(matched)} of {len(cv_skills)} CV skills. Missing key skills: {missing_str}"

    return score, matched, summary
