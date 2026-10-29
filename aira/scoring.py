"""Explainable, evidence-only job-to-candidate scoring.

Every dimension is scored from explicit evidence (resume text, verified
skills, the structured profile, or the posting itself). When the information
needed to score a dimension is absent, the dimension reports ``score: None``
with a ``missing_information`` message and is excluded from the weighted
total; nothing is ever defaulted to 0 or 100. Sensitive facts (work
authorization, citizenship, clearance) are never assumed.

The resume-coverage dimension reuses
:func:`linkedin_mcp_server.matching.score_keyword_evidence` rather than
re-implementing lexical coverage.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from typing import Any

from linkedin_mcp_server.matching import score_keyword_evidence

__all__ = [
    "ARCHITECT_PROFILES",
    "CLASSIFICATIONS",
    "DIMENSION_NAMES",
    "SCORING_VERSION",
    "SENIORITY_LEVELS",
    "SKILL_LEXICON",
    "ScoringWeights",
    "classify",
    "detect_seniority",
    "extract_job_skills",
    "extract_required_years",
    "score_job",
]

SCORING_VERSION = "explainable-match-v2"

CLASSIFICATIONS = (
    "Recommended",
    "Strong Match",
    "Good Match",
    "Stretch",
    "Not Recommended",
)

SENIORITY_LEVELS = (
    "intern",
    "junior",
    "mid",
    "senior",
    "staff",
    "principal",
    "lead",
    "manager",
    "director",
    "executive",
)
_SENIORITY_RANK: dict[str, int] = {
    "intern": 0,
    "junior": 1,
    "mid": 2,
    "senior": 3,
    "staff": 4,
    "lead": 4,
    "principal": 5,
    "manager": 6,
    "director": 7,
    "executive": 8,
}

ARCHITECT_PROFILES = frozenset(
    {
        "enterprise_architect",
        "solution_architect",
        "cloud_architect",
        "ai_architect",
        "principal_architect",
        "program_manager",
    }
)

DIMENSION_NAMES = (
    "required_skills",
    "preferred_skills",
    "experience",
    "seniority",
    "leadership_architecture",
    "location",
    "workplace",
    "salary",
    "work_authorization",
    "resume_coverage",
)
_DIMENSION_LABELS: dict[str, str] = {
    "required_skills": "Required skills",
    "preferred_skills": "Preferred skills",
    "experience": "Years of experience",
    "seniority": "Seniority alignment",
    "leadership_architecture": "Leadership and architecture scope",
    "location": "Location",
    "workplace": "Workplace type",
    "salary": "Salary alignment",
    "work_authorization": "Work authorization",
    "resume_coverage": "Resume keyword coverage",
}


@dataclass(frozen=True, slots=True)
class ScoringWeights:
    """Per-dimension weights; all finite and non-negative, at least one positive."""

    required_skills: float = 0.30
    preferred_skills: float = 0.10
    experience: float = 0.10
    seniority: float = 0.10
    leadership_architecture: float = 0.05
    location: float = 0.05
    workplace: float = 0.05
    salary: float = 0.05
    work_authorization: float = 0.05
    resume_coverage: float = 0.15

    def __post_init__(self) -> None:
        values = [float(getattr(self, name)) for name in DIMENSION_NAMES]
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Scoring weights must be finite and non-negative.")
        if sum(values) <= 0:
            raise ValueError("At least one scoring weight must be positive.")

    def as_dict(self) -> dict[str, float]:
        return {field.name: float(getattr(self, field.name)) for field in fields(self)}


# --------------------------------------------------------------------------- #
# Skill lexicon: canonical name -> aliases (matched whole-word, case-insensitive)
# --------------------------------------------------------------------------- #
SKILL_LEXICON: dict[str, tuple[str, ...]] = {
    # ---- cloud and infrastructure --------------------------------------------
    "aws": ("aws", "amazon web services"),
    "azure": ("azure", "microsoft azure"),
    "gcp": ("gcp", "google cloud", "google cloud platform"),
    "kubernetes": ("kubernetes", "k8s"),
    "eks": ("eks", "amazon eks", "elastic kubernetes service"),
    "aks": ("aks", "azure kubernetes service"),
    "gke": ("gke", "google kubernetes engine"),
    "docker": ("docker", "containers", "containerization", "containerisation"),
    "terraform": ("terraform",),
    "ansible": ("ansible",),
    "pulumi": ("pulumi",),
    "cloudformation": ("cloudformation", "aws cloudformation", "aws cdk", "cdk"),
    "helm": ("helm", "helm charts"),
    "istio": ("istio", "service mesh", "linkerd"),
    "openshift": ("openshift",),
    "serverless": (
        "serverless",
        "aws lambda",
        "lambda functions",
        "azure functions",
        "cloud functions",
    ),
    "ec2": ("ec2", "amazon ec2"),
    "s3": ("s3", "amazon s3"),
    "vpc": ("vpc", "virtual private cloud"),
    "cloud_architecture": (
        "cloud architecture",
        "cloud architect",
        "cloud native",
        "cloud-native",
    ),
    "multi_cloud": ("multi-cloud", "multicloud", "multi cloud", "hybrid cloud"),
    "cloud_migration": (
        "cloud migration",
        "migration to the cloud",
        "lift and shift",
        "lift-and-shift",
        "re-platform",
        "replatform",
        "application modernization",
        "app modernization",
    ),
    "finops": ("finops", "cloud cost optimization", "cloud cost management"),
    "landing_zone": (
        "landing zone",
        "landing zones",
        "control tower",
        "aws control tower",
    ),
    "well_architected": (
        "well-architected",
        "well architected",
        "aws well-architected framework",
        "azure well-architected framework",
    ),
    "vmware": ("vmware", "vsphere", "esxi"),
    "linux": ("linux", "rhel", "red hat", "ubuntu", "centos", "unix", "aix", "solaris"),
    "windows_server": ("windows server", "windows administration"),
    "active_directory": (
        "active directory",
        "azure ad",
        "entra id",
        "microsoft entra",
        "ldap",
    ),
    "networking": (
        "networking",
        "tcp/ip",
        "network architecture",
        "load balancing",
        "load balancer",
        "vpn",
        "sd-wan",
    ),
    "devops": ("devops", "devsecops"),
    "ci_cd": (
        "ci/cd",
        "cicd",
        "ci cd",
        "continuous integration",
        "continuous delivery",
        "continuous deployment",
    ),
    "jenkins": ("jenkins",),
    "github_actions": ("github actions",),
    "gitlab_ci": ("gitlab ci", "gitlab pipelines"),
    "azure_devops": ("azure devops", "azure pipelines", "ado pipelines"),
    "argo_cd": ("argocd", "argo cd", "argo workflows"),
    "observability": (
        "observability",
        "monitoring",
        "apm",
        "distributed tracing",
        "opentelemetry",
    ),
    "prometheus": ("prometheus",),
    "grafana": ("grafana",),
    "datadog": ("datadog",),
    "splunk": ("splunk",),
    "new_relic": ("new relic",),
    "elasticsearch": (
        "elasticsearch",
        "elastic search",
        "opensearch",
        "elk",
        "elk stack",
        "kibana",
        "logstash",
    ),
    "sre": (
        "sre",
        "site reliability",
        "site reliability engineering",
        "slo",
        "slos",
        "error budget",
    ),
    "infrastructure_as_code": ("infrastructure as code", "iac"),
    "gitops": ("gitops",),
    "kafka": ("kafka", "apache kafka", "confluent"),
    "rabbitmq": ("rabbitmq", "amqp"),
    "sqs_sns": ("sqs", "sns", "amazon sqs", "amazon sns"),
    "pubsub": ("pub/sub", "pubsub", "google pub/sub"),
    "mqtt": ("mqtt",),
    "storage": ("object storage", "block storage", "san", "nas", "netapp"),
    "virtualization": (
        "virtualization",
        "virtualisation",
        "hyper-v",
        "kvm",
        "citrix",
        "vdi",
    ),
    "caching": ("caching", "cdn", "cloudfront", "akamai", "cloudflare"),
    "gpu_hpc": (
        "gpu",
        "gpus",
        "cuda",
        "hpc",
        "high performance computing",
        "high-performance computing",
        "nvidia",
    ),
    # ---- data, analytics and ML ----------------------------------------------
    "python": ("python", "python3", "python 3"),
    "pytorch": ("pytorch", "torch"),
    "tensorflow": ("tensorflow", "keras"),
    "scikit_learn": ("scikit-learn", "sklearn", "scikit learn"),
    "pandas": ("pandas",),
    "numpy": ("numpy",),
    "spark": ("spark", "apache spark", "pyspark"),
    "hadoop": ("hadoop", "hdfs", "mapreduce"),
    "hive": ("hive", "apache hive"),
    "airflow": ("airflow", "apache airflow"),
    "dbt": ("dbt",),
    "snowflake": ("snowflake",),
    "databricks": ("databricks", "delta lake"),
    "bigquery": ("bigquery",),
    "redshift": ("redshift", "amazon redshift"),
    "synapse": ("synapse", "azure synapse"),
    "data_factory": ("azure data factory", "adf"),
    "glue": ("aws glue",),
    "emr": ("amazon emr", "aws emr", "elastic mapreduce"),
    "kinesis": ("kinesis", "amazon kinesis"),
    "data_lake": ("data lake", "data lakes", "data lakehouse", "lakehouse"),
    "data_warehouse": ("data warehouse", "data warehousing", "dwh", "edw"),
    "etl": ("etl", "elt", "data pipelines", "data pipeline"),
    "data_modeling": (
        "data modeling",
        "data modelling",
        "dimensional modeling",
        "star schema",
    ),
    "data_governance": (
        "data governance",
        "data catalog",
        "data lineage",
        "data quality",
        "master data management",
        "mdm",
    ),
    "data_engineering": ("data engineering", "data engineer"),
    "data_science": ("data science", "data scientist"),
    "machine_learning": ("machine learning", "ml", "ml models"),
    "artificial_intelligence": (
        "artificial intelligence",
        "ai",
        "ai/ml",
        "ai ml",
        "ml/ai",
    ),
    "deep_learning": ("deep learning", "neural networks", "neural network"),
    "nlp": ("nlp", "natural language processing"),
    "computer_vision": ("computer vision", "image recognition", "opencv"),
    "llm": (
        "llm",
        "llms",
        "large language model",
        "large language models",
        "foundation models",
        "foundation model",
    ),
    "generative_ai": ("generative ai", "genai", "gen ai", "gen-ai"),
    "rag": ("rag", "retrieval augmented generation", "retrieval-augmented generation"),
    "vector_db": (
        "vector database",
        "vector databases",
        "vector search",
        "pinecone",
        "weaviate",
        "milvus",
        "pgvector",
        "faiss",
    ),
    "langchain": ("langchain", "llamaindex", "llama index"),
    "prompt_engineering": ("prompt engineering", "prompting"),
    "fine_tuning": ("fine-tuning", "fine tuning", "lora", "peft"),
    "openai": ("openai", "gpt", "gpt-4", "chatgpt", "azure openai"),
    "anthropic": ("anthropic", "claude"),
    "hugging_face": ("hugging face", "huggingface", "transformers"),
    "bedrock": ("bedrock", "amazon bedrock", "aws bedrock"),
    "vertex_ai": ("vertex ai",),
    "sagemaker": ("sagemaker", "amazon sagemaker"),
    "azure_ml": ("azure ml", "azure machine learning"),
    "mlops": (
        "mlops",
        "ml ops",
        "mlflow",
        "kubeflow",
        "model deployment",
        "model monitoring",
        "model serving",
    ),
    "feature_store": ("feature store", "feast"),
    "responsible_ai": ("responsible ai", "ai ethics", "ai governance", "model risk"),
    "ai_agents": (
        "ai agents",
        "agentic",
        "agentic ai",
        "multi-agent",
        "multi agent",
        "autonomous agents",
    ),
    "mcp": ("model context protocol", "mcp"),
    "reinforcement_learning": ("reinforcement learning", "rl"),
    "statistics": ("statistics", "statistical modeling", "statistical analysis"),
    "experimentation": ("a/b testing", "ab testing", "experimentation"),
    "tableau": ("tableau",),
    "power_bi": ("power bi", "powerbi"),
    "looker": ("looker", "looker studio"),
    "qlik": ("qlik", "qlikview", "qlik sense"),
    "sql": ("sql", "t-sql", "pl/sql", "plsql", "tsql"),
    "postgresql": ("postgresql", "postgres"),
    "mysql": ("mysql", "mariadb"),
    "sql_server": ("sql server", "mssql", "microsoft sql server"),
    "oracle_db": ("oracle database", "oracle db", "oracle"),
    "mongodb": ("mongodb", "mongo"),
    "cassandra": ("cassandra",),
    "dynamodb": ("dynamodb",),
    "redis": ("redis", "memcached"),
    "cosmos_db": ("cosmos db", "cosmosdb"),
    "neo4j": ("neo4j", "graph database", "graph databases"),
    "streaming": (
        "stream processing",
        "streaming",
        "real-time data",
        "real time data",
        "flink",
        "apache flink",
        "spark streaming",
        "kafka streams",
    ),
    "big_data": ("big data", "large-scale data", "petabyte", "petabyte-scale"),
    "data_visualization": (
        "data visualization",
        "data visualisation",
        "matplotlib",
        "seaborn",
        "plotly",
        "d3",
        "d3.js",
    ),
    "jupyter": ("jupyter", "jupyter notebooks", "notebooks", "colab"),
    # ---- architecture ---------------------------------------------------------
    "enterprise_architecture": ("enterprise architecture", "enterprise architect"),
    "solution_architecture": (
        "solution architecture",
        "solutions architecture",
        "solution architect",
        "solutions architect",
        "solution design",
    ),
    "togaf": ("togaf", "togaf 9", "togaf 10", "togaf certified"),
    "zachman": ("zachman",),
    "archimate": ("archimate",),
    "microservices": (
        "microservices",
        "micro-services",
        "microservice",
        "micro services",
    ),
    "event_driven": (
        "event-driven",
        "event driven",
        "event-driven architecture",
        "event sourcing",
        "cqrs",
    ),
    "domain_driven_design": (
        "domain-driven design",
        "domain driven design",
        "ddd",
        "bounded context",
        "bounded contexts",
    ),
    "api_design": (
        "api design",
        "api development",
        "apis",
        "api",
        "rest api",
        "rest apis",
        "restful",
        "restful api",
        "restful apis",
        "restful services",
        "openapi",
        "swagger",
        "web services",
    ),
    "api_gateway": (
        "api gateway",
        "api management",
        "apigee",
        "kong",
        "azure api management",
    ),
    "mulesoft": ("mulesoft", "mule esb", "anypoint"),
    "graphql": ("graphql",),
    "grpc": ("grpc", "protobuf", "protocol buffers"),
    "soa": (
        "soa",
        "service-oriented architecture",
        "service oriented architecture",
        "esb",
        "enterprise service bus",
        "soap",
        "wsdl",
    ),
    "integration": (
        "integration architecture",
        "system integration",
        "systems integration",
        "integration patterns",
        "ipaas",
        "boomi",
        "dell boomi",
    ),
    "distributed_systems": (
        "distributed systems",
        "distributed computing",
        "scalability",
        "high availability",
        "fault tolerance",
        "fault-tolerant",
    ),
    "system_design": (
        "system design",
        "systems design",
        "architecture design",
        "technical design",
    ),
    "reference_architecture": (
        "reference architecture",
        "reference architectures",
        "architecture patterns",
        "design patterns",
    ),
    "architecture_governance": (
        "architecture governance",
        "architecture review board",
        "arb",
        "technical governance",
        "architecture standards",
    ),
    "technology_roadmap": (
        "technology roadmap",
        "technical roadmap",
        "roadmap",
        "roadmaps",
        "roadmapping",
    ),
    "business_capability": (
        "business capability",
        "business capabilities",
        "capability model",
        "capability mapping",
    ),
    "legacy_modernization": (
        "legacy modernization",
        "modernization",
        "modernisation",
        "legacy systems",
        "mainframe modernization",
    ),
    "mainframe": ("mainframe", "cobol", "z/os", "as/400", "ibm i"),
    "erp": ("erp", "enterprise resource planning"),
    "crm": ("crm", "customer relationship management"),
    "middleware": ("middleware", "message queues", "message queue", "jms"),
    "digital_transformation": (
        "digital transformation",
        "transformation program",
        "transformation programme",
        "it transformation",
    ),
    "tech_strategy": (
        "technology strategy",
        "it strategy",
        "technical strategy",
        "technology vision",
    ),
    "business_case": (
        "tco",
        "total cost of ownership",
        "roi",
        "business case",
        "business cases",
    ),
    "vendor_management": (
        "vendor management",
        "vendor evaluation",
        "rfp",
        "rfi",
        "vendor selection",
    ),
    "proof_of_concept": (
        "proof of concept",
        "proofs of concept",
        "poc",
        "pocs",
        "prototype",
        "prototyping",
    ),
    "non_functional_requirements": (
        "non-functional requirements",
        "nfrs",
        "nfr",
        "performance requirements",
        "resiliency",
        "resilience",
    ),
    "performance_tuning": (
        "performance tuning",
        "performance optimization",
        "performance optimisation",
        "profiling",
    ),
    "uml": (
        "uml",
        "sequence diagrams",
        "sequence diagram",
        "c4 model",
        "architecture diagrams",
        "diagramming",
        "visio",
        "lucidchart",
    ),
    "documentation": (
        "documentation",
        "technical documentation",
        "technical writing",
        "architecture decision records",
        "adr",
        "adrs",
        "design documents",
        "design docs",
    ),
    # ---- enterprise platforms ---------------------------------------------------
    "salesforce": (
        "salesforce",
        "sfdc",
        "salesforce platform",
        "apex",
        "lightning web components",
        "lwc",
        "sales cloud",
        "service cloud",
    ),
    "sap": ("sap", "sap s/4hana", "s/4hana", "sap hana", "sap ecc", "sap erp", "abap"),
    "servicenow": ("servicenow", "service now", "itsm", "itom"),
    "workday": ("workday", "workday hcm"),
    "oracle_cloud": (
        "oracle cloud",
        "oci",
        "oracle fusion",
        "oracle ebs",
        "e-business suite",
    ),
    "dynamics_365": ("dynamics 365", "microsoft dynamics", "dynamics crm", "d365"),
    "microsoft_365": ("sharepoint", "microsoft 365", "m365", "office 365", "o365"),
    "power_platform": ("power platform", "power apps", "powerapps", "power automate"),
    "informatica": ("informatica", "informatica powercenter", "iics"),
    "tibco": ("tibco",),
    "netsuite": ("netsuite",),
    "hubspot": ("hubspot",),
    "adobe_experience": ("adobe experience manager", "aem", "adobe experience cloud"),
    "guidewire": ("guidewire",),
    "pega": ("pega", "pegasystems", "pega platform"),
    "low_code": (
        "appian",
        "low-code",
        "low code",
        "no-code",
        "no code",
        "outsystems",
        "mendix",
    ),
    "rpa": (
        "uipath",
        "rpa",
        "robotic process automation",
        "automation anywhere",
        "blue prism",
    ),
    # ---- languages and frameworks ----------------------------------------------
    "java": ("java", "java 8", "java 11", "java 17", "java 21", "jvm"),
    "spring": (
        "spring boot",
        "springboot",
        "spring framework",
        "spring cloud",
        "spring mvc",
    ),
    "kotlin": ("kotlin",),
    "scala": ("scala",),
    "csharp": ("c#", "csharp", "c sharp"),
    "dotnet": (
        ".net",
        ".net core",
        "dotnet",
        "asp.net",
        "asp.net core",
        ".net framework",
        ".net 6",
        ".net 8",
    ),
    "cpp": ("c++", "cpp", "c/c++"),
    "c_language": ("c language", "c programming", "embedded c", "ansi c"),
    "go": ("golang", "go lang"),
    "rust": ("rust",),
    "javascript": ("javascript", "js", "es6", "ecmascript"),
    "typescript": ("typescript",),
    "node": ("node.js", "nodejs", "node js", "express.js", "expressjs"),
    "react": (
        "react",
        "react.js",
        "reactjs",
        "react js",
        "react native",
        "next.js",
        "nextjs",
    ),
    "angular": ("angular", "angularjs", "angular.js"),
    "vue": ("vue", "vue.js", "vuejs", "nuxt"),
    "html_css": ("html", "css", "html5", "css3", "sass", "tailwind"),
    "php": ("php", "laravel", "symfony"),
    "ruby": ("ruby", "ruby on rails", "rails"),
    "swift": ("swift", "ios development", "ios", "swiftui", "objective-c"),
    "android": ("android", "android development", "jetpack compose"),
    "flutter": ("flutter", "dart"),
    "django": ("django",),
    "flask": ("flask",),
    "fastapi": ("fastapi",),
    "bash": ("bash", "shell scripting", "shell script", "shell scripts", "unix shell"),
    "powershell": ("powershell",),
    "perl": ("perl",),
    "matlab": ("matlab",),
    "r_language": ("r language", "r programming", "rstudio", "tidyverse"),
    "git": ("git", "github", "bitbucket", "version control", "source control"),
    "test_automation": (
        "test automation",
        "automated testing",
        "selenium",
        "cypress",
        "playwright",
        "junit",
        "pytest",
        "jest",
    ),
    "tdd": (
        "tdd",
        "test-driven development",
        "test driven development",
        "bdd",
        "behavior-driven development",
        "unit testing",
        "unit tests",
    ),
    "code_review": ("code review", "code reviews", "pull requests", "pull request"),
    "oop": (
        "object-oriented",
        "object oriented",
        "oop",
        "oo design",
        "solid principles",
    ),
    "functional_programming": ("functional programming",),
    "algorithms": ("data structures", "algorithms", "data structures and algorithms"),
    "data_formats": ("xml", "json", "yaml", "avro", "parquet"),
    "mobile": ("mobile development", "mobile applications", "mobile apps"),
    "embedded": ("embedded systems", "embedded software", "firmware", "rtos"),
    "iot": ("iot", "internet of things", "edge computing", "iiot"),
    "blockchain": ("blockchain", "smart contracts", "solidity", "web3", "ethereum"),
    # ---- security ---------------------------------------------------------------
    "iam": (
        "iam",
        "identity and access management",
        "identity management",
        "identity & access management",
    ),
    "oauth": (
        "oauth",
        "oauth2",
        "oauth 2.0",
        "openid connect",
        "oidc",
        "saml",
        "sso",
        "single sign-on",
        "single sign on",
        "jwt",
    ),
    "zero_trust": ("zero trust", "zero-trust", "zta"),
    "security_architecture": (
        "security architecture",
        "security architect",
        "secure design",
        "secure by design",
        "threat modeling",
        "threat modelling",
    ),
    "appsec": (
        "application security",
        "appsec",
        "owasp",
        "sast",
        "dast",
        "secure coding",
        "penetration testing",
        "pen testing",
        "pentest",
    ),
    "cloud_security": ("cloud security", "cspm", "cnapp", "cloud security posture"),
    "network_security": (
        "network security",
        "firewall",
        "firewalls",
        "waf",
        "ids/ips",
        "intrusion detection",
    ),
    "encryption": (
        "encryption",
        "cryptography",
        "pki",
        "tls",
        "ssl",
        "key management",
        "kms",
        "hsm",
    ),
    "compliance": (
        "compliance",
        "regulatory compliance",
        "audit",
        "audits",
        "auditing",
    ),
    "soc2_iso27001": (
        "soc 2",
        "soc2",
        "soc 2 type ii",
        "soc 2 type 2",
        "iso 27001",
        "iso27001",
    ),
    "nist": ("nist", "nist csf", "nist 800-53", "fedramp", "cmmc"),
    "pci_dss": ("pci dss", "pci-dss", "pci"),
    "hipaa": ("hipaa",),
    "privacy": ("gdpr", "ccpa", "data privacy", "privacy", "data protection"),
    "siem": (
        "siem",
        "security operations center",
        "security operations",
        "splunk es",
        "microsoft sentinel",
        "azure sentinel",
        "qradar",
    ),
    "vulnerability_management": (
        "vulnerability management",
        "vulnerability scanning",
        "qualys",
        "tenable",
        "nessus",
        "crowdstrike",
        "edr",
    ),
    "secrets_management": ("secrets management", "hashicorp vault", "secrets manager"),
    "security_certifications": ("cissp", "cism", "cisa", "ccsp", "security+", "ceh"),
    "risk_management": (
        "risk management",
        "risk assessment",
        "risk assessments",
        "third-party risk",
        "information security risk",
        "risk register",
    ),
    "business_continuity": (
        "business continuity",
        "disaster recovery",
        "bcp",
        "backup and recovery",
        "rto",
        "rpo",
    ),
    # ---- delivery and process --------------------------------------------------
    "agile": (
        "agile",
        "agile methodologies",
        "agile methodology",
        "agile delivery",
        "sprint",
        "sprints",
    ),
    "scrum": ("scrum", "scrum master", "csm", "psm"),
    "kanban": ("kanban",),
    "safe": ("safe", "scaled agile", "scaled agile framework", "safe agilist", "spc"),
    "pmp": ("pmp", "pmi", "project management professional", "prince2", "capm"),
    "jira": ("jira", "atlassian"),
    "confluence": ("confluence", "wiki"),
    "project_management": (
        "project management",
        "project manager",
        "project planning",
        "project delivery",
        "project plans",
    ),
    "program_management": (
        "program management",
        "programme management",
        "program manager",
        "programme manager",
        "program delivery",
        "program governance",
        "technical program manager",
        "tpm",
    ),
    "portfolio_management": (
        "portfolio management",
        "portfolio",
        "ppm",
        "portfolio governance",
        "lean portfolio management",
    ),
    "pmo": ("pmo", "project management office", "portfolio management office"),
    "itil": (
        "itil",
        "itil v4",
        "itil 4",
        "service management",
        "it service management",
    ),
    "change_management": (
        "change management",
        "organizational change management",
        "ocm",
        "change control",
    ),
    "release_management": ("release management", "release planning", "release train"),
    "sdlc": (
        "waterfall",
        "sdlc",
        "software development lifecycle",
        "software development life cycle",
    ),
    "lean": ("lean", "six sigma", "lean six sigma", "kaizen", "continuous improvement"),
    "okrs": ("okrs", "okr", "kpis", "kpi", "key performance indicators"),
    "budgeting": (
        "budget",
        "budgeting",
        "budget management",
        "financial management",
        "p&l",
        "cost management",
    ),
    "resource_management": (
        "resource management",
        "resource planning",
        "capacity planning",
        "staffing",
    ),
    "raid": (
        "raid log",
        "raid",
        "risks and issues",
        "issue management",
        "dependency management",
    ),
    "status_reporting": (
        "status reporting",
        "status reports",
        "executive reporting",
        "dashboards",
        "dashboard",
    ),
    "estimation": ("estimation", "estimating", "story points", "planning poker"),
    "requirements": (
        "requirements gathering",
        "requirements analysis",
        "business requirements",
        "functional requirements",
        "user stories",
        "user story",
        "acceptance criteria",
        "brd",
    ),
    "business_analysis": (
        "business analysis",
        "business analyst",
        "process mapping",
        "process improvement",
        "bpmn",
        "business process",
    ),
    "product_management": (
        "product management",
        "product manager",
        "product owner",
        "product roadmap",
        "product strategy",
        "backlog",
        "backlog management",
        "prioritization",
    ),
    "ms_office": (
        "excel",
        "microsoft excel",
        "powerpoint",
        "ms project",
        "microsoft project",
        "smartsheet",
        "ms office",
        "microsoft office",
    ),
    # ---- leadership and soft skills ----------------------------------------------
    "leadership": (
        "leadership",
        "team leadership",
        "technical leadership",
        "leading teams",
        "lead teams",
        "people leadership",
    ),
    "mentoring": ("mentoring", "mentorship", "coaching", "mentor", "coach"),
    "stakeholder_management": (
        "stakeholder management",
        "stakeholder",
        "stakeholders",
        "stakeholder engagement",
        "stakeholder communication",
    ),
    "executive_communication": (
        "executive communication",
        "executive presence",
        "c-level",
        "c-suite",
        "board-level",
        "executive stakeholders",
        "executive leadership",
        "senior leadership",
    ),
    "communication": (
        "communication",
        "communication skills",
        "verbal and written",
        "presentation skills",
        "presentations",
        "written communication",
    ),
    "cross_functional": (
        "cross-functional",
        "cross functional",
        "cross-functional teams",
        "matrix organization",
        "matrixed",
    ),
    "negotiation": ("negotiation", "negotiating", "contract negotiation", "contracts"),
    "customer_facing": (
        "customer-facing",
        "customer facing",
        "client-facing",
        "client facing",
        "customer engagement",
        "client engagement",
        "consulting",
        "consultative",
        "advisory",
    ),
    "pre_sales": (
        "pre-sales",
        "presales",
        "solution selling",
        "sales engineering",
        "sales engineer",
        "demos",
        "proposal",
        "proposals",
        "sow",
        "statement of work",
    ),
    "strategic_thinking": (
        "strategic thinking",
        "strategic planning",
        "strategic vision",
        "strategic initiatives",
        "strategic direction",
    ),
    "problem_solving": (
        "problem solving",
        "problem-solving",
        "analytical",
        "analytical skills",
        "critical thinking",
        "troubleshooting",
        "root cause analysis",
        "rca",
    ),
    "collaboration": ("collaboration", "collaborative", "teamwork", "team player"),
    "team_building": (
        "hiring",
        "recruiting",
        "interviewing",
        "talent development",
        "performance management",
        "performance reviews",
        "building teams",
        "team building",
    ),
    "innovation": (
        "innovation",
        "innovative",
        "emerging technologies",
        "emerging technology",
        "thought leadership",
    ),
    "ownership": (
        "ownership",
        "accountability",
        "self-starter",
        "self starter",
        "autonomous",
        "independently",
        "initiative",
    ),
    "decision_making": (
        "decision making",
        "decision-making",
        "trade-offs",
        "tradeoffs",
        "trade offs",
    ),
    "influence": (
        "influence",
        "influencing",
        "influence without authority",
        "persuasion",
        "consensus",
        "buy-in",
    ),
    "time_management": (
        "time management",
        "prioritisation",
        "multitasking",
        "deadlines",
        "fast-paced",
        "fast paced",
    ),
    "conflict_resolution": (
        "conflict resolution",
        "conflict management",
        "difficult conversations",
    ),
    # ---- domains ----------------------------------------------------------------
    "healthcare": (
        "healthcare",
        "health care",
        "hl7",
        "fhir",
        "ehr",
        "clinical",
        "life sciences",
        "pharma",
        "pharmaceutical",
        "medical devices",
    ),
    "financial_services": (
        "fintech",
        "banking",
        "financial services",
        "payments",
        "payment processing",
        "capital markets",
        "trading",
        "insurance",
        "wealth management",
        "lending",
    ),
    "retail": ("retail", "e-commerce", "ecommerce", "e commerce", "omnichannel"),
    "supply_chain": (
        "supply chain",
        "logistics",
        "warehouse management",
        "wms",
        "order management",
        "oms",
        "inventory management",
    ),
    "telecom": ("telecom", "telecommunications", "5g", "oss/bss", "bss"),
    "public_sector": (
        "public sector",
        "government",
        "federal",
        "state and local",
        "dod",
        "department of defense",
        "civilian agencies",
    ),
    "energy": (
        "energy",
        "utilities",
        "oil and gas",
        "oil & gas",
        "renewables",
        "smart grid",
    ),
    "manufacturing": (
        "manufacturing",
        "industry 4.0",
        "mes",
        "plm",
        "scada",
        "operational technology",
    ),
    "education": ("edtech", "higher education", "k-12"),
    "automotive": ("automotive", "autonomous vehicles", "adas"),
    # ---- certifications -------------------------------------------------------------
    "aws_certified": (
        "aws certified",
        "aws certified solutions architect",
        "aws solutions architect professional",
        "aws solutions architect associate",
        "aws certification",
        "aws certifications",
    ),
    "azure_certified": (
        "azure certified",
        "az-305",
        "az-304",
        "az-900",
        "az-104",
        "az-400",
        "azure solutions architect expert",
        "microsoft certified",
    ),
    "gcp_certified": (
        "google cloud certified",
        "professional cloud architect",
        "gcp certification",
    ),
    "kubernetes_certified": (
        "cka",
        "ckad",
        "cks",
        "certified kubernetes administrator",
    ),
    "terraform_certified": ("terraform associate", "hashicorp certified"),
    "cisco_certified": ("ccna", "ccnp", "ccie", "cisco certified"),
}


def _alias_pattern(aliases: Sequence[str]) -> re.Pattern[str]:
    ordered = sorted(
        {alias.casefold() for alias in aliases}, key=lambda item: (-len(item), item)
    )
    body = "|".join(re.escape(alias) for alias in ordered)
    return re.compile(rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])")


_ALIAS_PATTERNS: dict[str, re.Pattern[str]] = {
    canonical: _alias_pattern(aliases) for canonical, aliases in SKILL_LEXICON.items()
}
_ALIAS_TO_CANONICAL: dict[str, str] = {
    alias.casefold(): canonical
    for canonical, aliases in SKILL_LEXICON.items()
    for alias in aliases
}

_WHITESPACE_RE = re.compile(r"\s+")
_UNICODE_FIXES = str.maketrans({"–": "-", "—": "-", "’": "'", "‘": "'", " ": " "})


def _normalize_text(text: Any) -> str:
    return _WHITESPACE_RE.sub(
        " ", str(text or "").translate(_UNICODE_FIXES).casefold()
    ).strip()


def _find_skills(text_norm: str) -> list[str]:
    """Canonical skills present in normalized text, ordered by first occurrence."""
    hits: list[tuple[int, str]] = []
    for canonical, pattern in _ALIAS_PATTERNS.items():
        match = pattern.search(text_norm)
        if match is not None:
            hits.append((match.start(), canonical))
    hits.sort()
    return [canonical for _, canonical in hits]


def _canonicalize_skill(value: Any) -> list[str]:
    """Map a free-form skill string to canonical lexicon names (or itself)."""
    text = _normalize_text(value)
    if not text:
        return []
    if text in SKILL_LEXICON:
        return [text]
    direct = _ALIAS_TO_CANONICAL.get(text)
    if direct is not None:
        return [direct]
    found = _find_skills(text)
    return found or [text]


def _phrase_in_text(phrase: str, text_norm: str) -> bool:
    return (
        re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text_norm)
        is not None
    )


# --------------------------------------------------------------------------- #
# Job description parsing: sections, years, seniority
# --------------------------------------------------------------------------- #
_PREFERRED_HEADING_RE = re.compile(
    r"\b(preferred|nice[- ]to[- ]have|bonus|plus|desired|ideal|ideally|good to have|great to have"
    r"|additionally|extra credit|would be great|not required|optional|advantageous|desirable)\b"
)
_REQUIRED_HEADING_RE = re.compile(
    r"\b(requirements?|required|must[- ]haves?|qualifications?|what you bring|what you'?ll need"
    r"|what you need|what we need|minimum|basic qualifications|who you are|skills and experience"
    r"|skills & experience|your profile|about you|what we'?re looking for|key skills|essential"
    r"|experience required|required skills|technical skills|you have|you bring|what you have)\b"
)
_IGNORE_HEADING_RE = re.compile(
    r"\b(benefits|perks|about (us|the company|the team)|equal opportunity|eeo|compensation"
    r"|salary|pay range|why join|what we offer|our company|who we are|how to apply|our values"
    r"|our culture|company overview|diversity|legal|disclaimer)\b"
)
_NEUTRAL_HEADING_RE = re.compile(
    r"\b(responsibilities|what you'?ll do|what you will do|the role|about the role|about the job"
    r"|job description|overview|role overview|your role|duties|key responsibilities|day to day"
    r"|in this role|your impact|what you'?ll be doing)\b"
)
_INLINE_PREFERRED_RE = re.compile(
    r"\b(preferred|nice[- ]to[- ]have|bonus|a plus|is a plus|desired|ideally|not required"
    r"|would be (?:a )?(?:plus|bonus|great|nice)|advantageous)\b"
)
_HEADING_SPLIT_RE = re.compile(
    r"(?<!\n)\s*(?=\b(?:preferred|nice[- ]to[- ]have|bonus|requirements?|required|qualifications"
    r"|must[- ]haves?|responsibilities|benefits|minimum qualifications|basic qualifications)"
    r"\b[^\n:.]{0,30}:)",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*(?:[-*•·▪◦‣o]|\d+[.)]|[a-z][.)])\s+")
_HEADING_DECOR_RE = re.compile(r"^[#*_\s]+|[#*_:\s]+$")
_HEAD_COLON_RE = re.compile(r"^([^:]{1,60}):\s*(.*)$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;!?])\s+(?=[A-Z(\"'])")


def _sentences(content: str) -> list[str]:
    return [
        piece.strip() for piece in _SENTENCE_SPLIT_RE.split(content) if piece.strip()
    ]


def _heading_mode(candidate: str) -> str | None:
    """Return the section mode a heading line switches to, or None if not a heading."""
    text = _HEADING_DECOR_RE.sub("", candidate.casefold()).strip()
    if not text or len(text.split()) > 6:
        return None
    if _PREFERRED_HEADING_RE.search(text) and not re.search(
        r"\b(?:a|is a) plus\b", text
    ):
        return "preferred"
    if _IGNORE_HEADING_RE.search(text):
        return "ignore"
    if _REQUIRED_HEADING_RE.search(text) or _NEUTRAL_HEADING_RE.search(text):
        return "required"
    return None


def _split_sections(description: str) -> list[tuple[str, str]]:
    """Split a description into (mode, line) pairs; mode is required/preferred/ignore."""
    text = str(description or "").translate(_UNICODE_FIXES).replace("\r\n", "\n")
    text = _HEADING_SPLIT_RE.sub("\n", text)
    mode = "required"
    lines: list[tuple[str, str]] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        bulleted = _BULLET_RE.match(line) is not None
        content = _BULLET_RE.sub("", line, count=1)
        colon = _HEAD_COLON_RE.match(content)
        if colon is not None:
            head_mode = _heading_mode(colon.group(1))
            if head_mode is not None:
                mode = head_mode
                lines.extend((mode, piece) for piece in _sentences(colon.group(2)))
                continue
        if not bulleted and (content.endswith(":") or len(content.split()) <= 4):
            head_mode = _heading_mode(content)
            if head_mode is not None:
                mode = head_mode
                continue
        lines.extend((mode, piece) for piece in _sentences(content))
    return lines


def extract_job_skills(description: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (required, preferred) canonical skills found in the description."""
    required: list[str] = []
    preferred: list[str] = []
    for mode, line in _split_sections(description):
        if mode == "ignore":
            continue
        line_norm = _normalize_text(line)
        skills = _find_skills(line_norm)
        if not skills:
            continue
        target = preferred
        if mode == "required" and _INLINE_PREFERRED_RE.search(line_norm) is None:
            target = required
        for skill in skills:
            if skill not in target:
                target.append(skill)
    preferred_only = tuple(skill for skill in preferred if skill not in required)
    return tuple(required), preferred_only


_WORD_NUMBERS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_NUM = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
_YEARS = r"(?:years?|yrs?)"
_YEARS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"\b(?:at least|minimum(?: of)?|min\.?(?: of)?|over|more than|no less than|a minimum of)"
        rf"\s+{_NUM}\s*\+?\s*{_YEARS}\b"
    ),
    re.compile(rf"\b{_NUM}\s*\+?\s*(?:-|to|–)\s*{_NUM}\s*\+?\s*{_YEARS}\b"),
    re.compile(rf"\b{_NUM}\s*(?:\+|plus|or more)\s*{_YEARS}\b"),
    re.compile(
        rf"\b{_NUM}\s*{_YEARS}'?\s*(?:of\s+)?(?:\w+\s+){{0,3}}?(?:experience|exp\b|background"
        r"|track record|working|hands-on|in\s|with\s|as\s|building|designing|developing|leading"
        r"|managing|delivering|architecting|supporting|implementing|operating)"
    ),
)


def _to_int(token: str) -> int | None:
    token = token.casefold()
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token)


def _first_years(text_norm: str) -> int | None:
    best: tuple[int, int] | None = None
    for pattern in _YEARS_PATTERNS:
        for match in pattern.finditer(text_norm):
            values = [
                value
                for value in (_to_int(group) for group in match.groups())
                if value is not None
            ]
            if not values:
                continue
            candidate = (match.start(), min(values))
            if best is None or candidate[0] < best[0]:
                best = candidate
    return None if best is None else best[1]


def extract_required_years(description: str) -> int | None:
    """Extract the first stated years-of-experience requirement (ranges use the minimum)."""
    sections = _split_sections(description)
    required_text = _normalize_text(
        " \n ".join(line for mode, line in sections if mode == "required")
    )
    years = _first_years(required_text)
    if years is not None:
        return years
    return _first_years(_normalize_text(description))


_TITLE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("intern", re.compile(r"(?<![a-z])(?:intern|internship|co-?op)(?![a-z])")),
    (
        "executive",
        re.compile(
            r"(?<![a-z])(?:vp|vice president|chief|cto|cio|ciso|cdo|cpo|coo|ceo|cfo|head of"
            r"|president|evp|svp|avp|c-level|executive)(?![a-z])"
        ),
    ),
    ("director", re.compile(r"(?<![a-z])director(?![a-z])")),
    ("manager", re.compile(r"(?<![a-z])(?:manager|mgr)(?![a-z])")),
    (
        "principal",
        re.compile(r"(?<![a-z])(?:principal|architect|distinguished|fellow)(?![a-z])"),
    ),
    ("staff", re.compile(r"(?<![a-z])staff(?![a-z])")),
    ("lead", re.compile(r"(?<![a-z])(?:lead|leader|tech lead|team lead)(?![a-z])")),
    ("senior", re.compile(r"(?<![a-z])(?:senior|sr\.?|snr)(?![a-z])")),
    (
        "junior",
        re.compile(
            r"(?<![a-z])(?:junior|jr\.?|associate|entry[- ]level|entry|graduate|trainee)(?![a-z])"
        ),
    ),
    ("mid", re.compile(r"(?<![a-z])(?:mid[- ]level|intermediate|mid)(?![a-z])")),
)


def detect_seniority(title: str, description: str = "") -> str | None:
    """Infer a seniority level from the title first, then from description years."""
    title_norm = _normalize_text(title)
    for level, pattern in _TITLE_RULES:
        if title_norm and pattern.search(title_norm):
            return level
    years = extract_required_years(description) if description else None
    if years is not None and years >= 10:
        return "senior"
    return None


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def classify(
    score: float | None, *, hard_blockers: Sequence[str] = ()
) -> tuple[str, str]:
    """Return (label, reason) for a score, honouring hard blockers first."""
    blockers = [str(item).strip() for item in hard_blockers if str(item).strip()]
    if blockers:
        return "Not Recommended", " ".join(blockers)
    if score is None:
        return "Stretch", "Not enough information to score this job yet."
    value = float(score)
    if value >= 85:
        return (
            "Recommended",
            f"Weighted evidence score {value:.2f} meets the Recommended threshold (85).",
        )
    if value >= 70:
        return (
            "Strong Match",
            f"Weighted evidence score {value:.2f} meets the Strong Match threshold (70).",
        )
    if value >= 55:
        return (
            "Good Match",
            f"Weighted evidence score {value:.2f} meets the Good Match threshold (55).",
        )
    if value >= 40:
        return (
            "Stretch",
            f"Weighted evidence score {value:.2f} meets the Stretch threshold (40).",
        )
    return (
        "Not Recommended",
        f"Weighted evidence score {value:.2f} is below the Stretch threshold (40).",
    )


# --------------------------------------------------------------------------- #
# Dimension helpers
# --------------------------------------------------------------------------- #
def _dimension(
    name: str,
    weight: float,
    *,
    score: float | None,
    reason: str,
    evidence: Sequence[Mapping[str, str]] = (),
    matched: Sequence[str] = (),
    missing: Sequence[str] = (),
    missing_information: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "label": _DIMENSION_LABELS[name],
        "score": None if score is None else round(float(score), 2),
        "weight": float(weight),
        "evidence": [dict(item) for item in evidence],
        "matched": list(matched),
        "missing": list(missing),
        "missing_information": missing_information,
        "reason": reason,
    }


def _unscored(name: str, weight: float, message: str) -> dict[str, Any]:
    return _dimension(
        name, weight, score=None, reason=message, missing_information=message
    )


def _str_list(value: Any) -> list[str]:
    if value is None or isinstance(value, str) or not isinstance(value, Sequence):
        return (
            []
            if value is None or not isinstance(value, str) or not value.strip()
            else [value]
        )
    return [str(item).strip() for item in value if str(item).strip()]


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip().replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", text)
    if match is None:
        return None
    number = float(match.group(0))
    if re.search(r"\d\s*k\b", text.casefold()):
        number *= 1000
    return number


def _candidate_skills(profile: Mapping[str, Any], resume_norm: str) -> dict[str, str]:
    """Canonical skill -> evidence source ('verified_skill' beats 'resume')."""
    sources: dict[str, str] = {}
    for raw in _str_list(profile.get("verified_skills")):
        for canonical in _canonicalize_skill(raw):
            sources[canonical] = "verified_skill"
    for canonical in _find_skills(resume_norm):
        sources.setdefault(canonical, "resume")
    return sources


def _skills_dimension(
    name: str,
    weight: float,
    job_skills: Sequence[str],
    candidate: Mapping[str, str],
    resume_norm: str,
    verified_norm: Sequence[str],
) -> dict[str, Any]:
    label = _DIMENSION_LABELS[name].lower()
    if not job_skills:
        return _unscored(
            name,
            weight,
            f"The posting lists no extractable {label}, so this dimension is not scored.",
        )
    matched: list[str] = []
    missing: list[str] = []
    evidence: list[dict[str, str]] = []
    for skill in job_skills:
        source = candidate.get(skill)
        if source is None and skill not in SKILL_LEXICON:
            if skill in verified_norm:
                source = "verified_skill"
            elif _phrase_in_text(skill, resume_norm):
                source = "resume"
        if source is None:
            missing.append(skill)
            continue
        matched.append(skill)
        where = "verified skills" if source == "verified_skill" else "resume text"
        evidence.append(
            {"fact": f"Skill '{skill}' found in {where}.", "source": source}
        )
    score = 100.0 * len(matched) / len(job_skills)
    return _dimension(
        name,
        weight,
        score=score,
        reason=f"{len(matched)} of {len(job_skills)} {label} are evidenced by the resume or verified skills.",
        evidence=evidence,
        matched=matched,
        missing=missing,
    )


def _experience_dimension(
    weight: float, description: str, profile: Mapping[str, Any]
) -> dict[str, Any]:
    name = "experience"
    required = extract_required_years(description)
    if required is None:
        return _unscored(
            name,
            weight,
            "The posting does not state a required number of years of experience.",
        )
    years = _as_number(profile.get("years_of_experience"))
    if years is None:
        return _unscored(
            name,
            weight,
            "Add your years of experience to the profile to assess this requirement.",
        )
    gap = required - years
    if gap <= 0:
        score, outcome = 100.0, "meets or exceeds"
    elif gap <= 1:
        score, outcome = 75.0, "is within one year of"
    elif gap <= 2:
        score, outcome = 50.0, "is within two years of"
    else:
        score, outcome = 25.0, "is more than two years below"
    years_text = f"{years:g}"
    return _dimension(
        name,
        weight,
        score=score,
        reason=f"Your {years_text} years {outcome} the {required}+ years the posting asks for.",
        evidence=[
            {
                "fact": f"The posting asks for {required}+ years of experience.",
                "source": "job",
            },
            {
                "fact": f"Your profile lists {years_text} years of experience.",
                "source": "profile",
            },
        ],
        matched=[f"{required}+ years"] if gap <= 0 else [],
        missing=[] if gap <= 0 else [f"{required}+ years"],
    )


def _seniority_dimension(
    weight: float, job: Mapping[str, Any], profile: Mapping[str, Any]
) -> dict[str, Any]:
    name = "seniority"
    job_level = _normalize_text(job.get("seniority")) or None
    detected = False
    if job_level not in _SENIORITY_RANK:
        job_level = detect_seniority(
            str(job.get("title") or ""), str(job.get("description") or "")
        )
        detected = True
    if job_level is None:
        return _unscored(
            name,
            weight,
            "The posting's seniority could not be determined from its title or description.",
        )
    target = _normalize_text(profile.get("target_seniority")) or None
    if target not in _SENIORITY_RANK:
        return _unscored(
            name, weight, "Add a target seniority to the profile to assess alignment."
        )
    distance = abs(_SENIORITY_RANK[job_level] - _SENIORITY_RANK[target])
    score = {0: 100.0, 1: 70.0, 2: 40.0}.get(distance, 10.0)
    job_fact = f"The posting reads as {job_level} level" + (
        " (inferred from the title/description)."
        if detected
        else " (stated by the source)."
    )
    return _dimension(
        name,
        weight,
        score=score,
        reason=f"Job level '{job_level}' is {distance} step(s) from your target '{target}'.",
        evidence=[
            {"fact": job_fact, "source": "job"},
            {"fact": f"Your target seniority is {target}.", "source": "profile"},
        ],
        matched=[job_level] if distance == 0 else [],
        missing=[] if distance == 0 else [job_level],
    )


_LEADERSHIP_TERMS: dict[str, tuple[str, ...]] = {
    "architecture": ("architecture", "architectural", "architect"),
    "roadmap": ("roadmap", "roadmaps", "roadmapping"),
    "governance": ("governance",),
    "stakeholder": ("stakeholder", "stakeholders"),
    "enterprise": ("enterprise", "enterprise-wide", "enterprise wide"),
    "solution design": (
        "solution design",
        "solutions design",
        "solution architecture",
        "solutions architecture",
    ),
    "cloud strategy": (
        "cloud strategy",
        "cloud-first",
        "cloud first",
        "cloud adoption",
    ),
    "program": ("program", "programs", "programme", "programmes", "program management"),
    "portfolio": ("portfolio", "portfolios"),
    "togaf": ("togaf",),
    "well-architected": ("well-architected", "well architected"),
    "reference architecture": ("reference architecture", "reference architectures"),
    "ml platform": (
        "ml platform",
        "ml platforms",
        "machine learning platform",
        "ai platform",
        "data platform",
    ),
    "llm": (
        "llm",
        "llms",
        "large language model",
        "large language models",
        "generative ai",
        "genai",
    ),
    "mlops": ("mlops", "ml ops", "model lifecycle"),
    "transformation": (
        "transformation",
        "digital transformation",
        "modernization",
        "modernisation",
    ),
    "executive": (
        "executive",
        "executives",
        "c-level",
        "c-suite",
        "cxo",
        "leadership team",
    ),
    "vendor": ("vendor", "vendors", "vendor management"),
    "business case": (
        "business case",
        "business cases",
        "tco",
        "roi",
        "total cost of ownership",
    ),
    "standards": (
        "standards",
        "principles",
        "guardrails",
        "design authority",
        "architecture review",
    ),
    "mentoring": ("mentor", "mentoring", "mentorship", "coaching"),
    "cross-functional": ("cross-functional", "cross functional", "matrix"),
    "leadership": ("leadership", "lead", "leading", "leads", "led"),
    "capability": ("capability", "capabilities", "capability model"),
    "operating model": ("operating model", "target operating model"),
    "migration": ("migration", "migrations", "migrate"),
    "integration": ("integration", "integrations", "interoperability"),
    "scale": ("at scale", "scalable", "scalability", "large-scale", "large scale"),
    "strategy": ("strategy", "strategic", "strategies"),
    "budget": ("budget", "budgets", "budgeting", "p&l"),
    "risk": ("risk", "risks", "risk management", "compliance"),
    "security": ("security", "secure", "zero trust"),
}
_LEADERSHIP_PATTERNS: dict[str, re.Pattern[str]] = {
    term: _alias_pattern(aliases) for term, aliases in _LEADERSHIP_TERMS.items()
}


def _leadership_dimension(
    weight: float,
    description: str,
    profile: Mapping[str, Any],
    resume_norm: str,
    verified_norm: str,
) -> dict[str, Any]:
    name = "leadership_architecture"
    career_profile = _normalize_text(profile.get("career_profile")) or None
    if career_profile not in ARCHITECT_PROFILES:
        return _unscored(
            name,
            weight,
            "Set an architect or program-manager career profile to assess leadership and architecture scope.",
        )
    description_norm = _normalize_text(description)
    job_terms = [
        term
        for term, pattern in _LEADERSHIP_PATTERNS.items()
        if pattern.search(description_norm)
    ]
    if not job_terms:
        return _unscored(
            name,
            weight,
            "The posting does not mention leadership or architecture scope terms, so this dimension is not scored.",
        )
    matched: list[str] = []
    missing: list[str] = []
    evidence: list[dict[str, str]] = []
    for term in job_terms:
        pattern = _LEADERSHIP_PATTERNS[term]
        if pattern.search(verified_norm):
            matched.append(term)
            evidence.append(
                {
                    "fact": f"Scope term '{term}' appears in your verified skills.",
                    "source": "verified_skill",
                }
            )
        elif pattern.search(resume_norm):
            matched.append(term)
            evidence.append(
                {
                    "fact": f"Scope term '{term}' appears in your resume.",
                    "source": "resume",
                }
            )
        else:
            missing.append(term)
    score = 100.0 * len(matched) / len(job_terms)
    return _dimension(
        name,
        weight,
        score=score,
        reason=f"{len(matched)} of {len(job_terms)} leadership/architecture scope terms in the posting are evidenced for the {career_profile} profile.",
        evidence=evidence,
        matched=matched,
        missing=missing,
    )


_WORKPLACE_ALIASES: dict[str, str] = {
    "remote": "remote",
    "fully remote": "remote",
    "work from home": "remote",
    "wfh": "remote",
    "hybrid": "hybrid",
    "on_site": "on_site",
    "onsite": "on_site",
    "on-site": "on_site",
    "on site": "on_site",
    "in-office": "on_site",
    "in office": "on_site",
    "office": "on_site",
}


def _normalize_workplace(value: Any) -> str | None:
    text = _normalize_text(value)
    return _WORKPLACE_ALIASES.get(text) if text else None


def _location_dimension(
    weight: float, job: Mapping[str, Any], profile: Mapping[str, Any]
) -> dict[str, Any]:
    name = "location"
    locations = [_normalize_text(item) for item in _str_list(profile.get("locations"))]
    locations = [item for item in locations if item]
    workplace_prefs = {
        _normalize_workplace(item) for item in _str_list(profile.get("workplace_types"))
    }
    job_location = _normalize_text(job.get("location"))
    job_workplace = _normalize_workplace(job.get("workplace_type"))
    candidate_remote = "remote" in workplace_prefs or any(
        item == "remote" for item in locations
    )
    job_remote = job_workplace == "remote" or _phrase_in_text("remote", job_location)
    if job_remote and candidate_remote:
        return _dimension(
            name,
            weight,
            score=100.0,
            reason="The posting is remote and your preferences include remote work.",
            evidence=[
                {"fact": "The posting is remote.", "source": "job"},
                {"fact": "Your preferences include remote work.", "source": "profile"},
            ],
            matched=["remote"],
        )
    if not locations or not job_location:
        return _unscored(
            name,
            weight,
            "Add a location preference and provide a job location to assess alignment.",
        )
    matched = [
        item for item in locations if item in job_location or job_location in item
    ]
    score = 100.0 if matched else 0.0
    return _dimension(
        name,
        weight,
        score=score,
        reason=(
            f"Job location '{job_location}' matches your preference '{matched[0]}'."
            if matched
            else f"Job location '{job_location}' does not match any of your location preferences."
        ),
        evidence=[
            {"fact": f"The posting location is '{job_location}'.", "source": "job"},
            {
                "fact": f"Your location preferences: {', '.join(locations)}.",
                "source": "profile",
            },
        ],
        matched=matched,
        missing=[item for item in locations if item not in matched],
    )


def _workplace_dimension(
    weight: float, job: Mapping[str, Any], profile: Mapping[str, Any]
) -> dict[str, Any]:
    name = "workplace"
    job_workplace = _normalize_workplace(job.get("workplace_type"))
    if job_workplace is None:
        return _unscored(
            name,
            weight,
            "The source did not provide a structured workplace type for this posting.",
        )
    prefs = [
        item
        for item in (
            _normalize_workplace(value)
            for value in _str_list(profile.get("workplace_types"))
        )
        if item
    ]
    if not prefs:
        return _unscored(
            name,
            weight,
            "Add a workplace preference (remote, hybrid, on_site) to assess alignment.",
        )
    score = 100.0 if job_workplace in prefs else 0.0
    return _dimension(
        name,
        weight,
        score=score,
        reason=f"The posting is {job_workplace}; your preferences are {', '.join(prefs)}.",
        evidence=[
            {
                "fact": f"The posting workplace type is {job_workplace}.",
                "source": "job",
            },
            {
                "fact": f"Your workplace preferences: {', '.join(prefs)}.",
                "source": "profile",
            },
        ],
        matched=[job_workplace] if score else [],
        missing=[] if score else [job_workplace],
    )


def _salary_dimension(
    weight: float, job: Mapping[str, Any], profile: Mapping[str, Any]
) -> dict[str, Any]:
    name = "salary"
    exp_min = _as_number(profile.get("salary_expectation_min"))
    exp_max = _as_number(profile.get("salary_expectation_max"))
    job_min = _as_number(job.get("salary_min"))
    job_max = _as_number(job.get("salary_max"))
    if exp_min is None and exp_max is None:
        return _unscored(
            name,
            weight,
            "Add a salary expectation to the profile to assess compensation alignment.",
        )
    if job_min is None and job_max is None:
        return _unscored(name, weight, "The posting does not state a salary range.")
    job_currency = _normalize_text(job.get("currency")).upper()
    profile_currency = _normalize_text(profile.get("currency")).upper()
    if job_currency and profile_currency and job_currency != profile_currency:
        return _unscored(
            name,
            weight,
            f"The posting is in {job_currency} but your expectation is in {profile_currency}; currencies are not converted.",
        )
    exp_lo = exp_min if exp_min is not None else exp_max
    exp_hi = exp_max if exp_max is not None else math.inf
    job_lo = job_min if job_min is not None else job_max
    job_hi = job_max if job_max is not None else job_min
    assert exp_lo is not None and job_lo is not None and job_hi is not None
    evidence = [
        {
            "fact": f"The posting range is {job_lo:,.0f}-{job_hi:,.0f} {job_currency or ''}".rstrip()
            + ".",
            "source": "job",
        },
        {
            "fact": f"Your expectation is {exp_lo:,.0f}"
            + (f"-{exp_hi:,.0f}" if math.isfinite(exp_hi) else "+")
            + f" {profile_currency or ''}".rstrip()
            + ".",
            "source": "profile",
        },
    ]
    if not job_currency or not profile_currency:
        evidence.append(
            {
                "fact": "Currency was not stated on both sides; amounts were compared as the same currency.",
                "source": "job",
            }
        )
    if job_hi >= exp_lo and job_lo <= exp_hi:
        score, reason = 100.0, "The posted range overlaps your expectation."
    elif job_lo > exp_hi:
        score, reason = 100.0, "The posted range is above your stated expectation."
    elif job_hi >= exp_lo * 0.9:
        score, reason = (
            60.0,
            "The posted maximum is within 10% below your minimum expectation.",
        )
    else:
        score, reason = (
            20.0,
            "The posted maximum is more than 10% below your minimum expectation.",
        )
    return _dimension(
        name,
        weight,
        score=score,
        reason=reason,
        evidence=evidence,
        matched=["range"] if score == 100 else [],
        missing=[] if score == 100 else ["range"],
    )


_NO_SPONSORSHIP_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:no|not|without|unable to|cannot|can't|will not|won't|do(?:es)? not|not able to|not in a position to)\s+(?:\w+\s+){0,3}?sponsor"
    ),
    re.compile(
        r"\bsponsorship\s+(?:is|will|would)\s+not\s+(?:be\s+)?(?:available|provided|offered|possible|considered)"
    ),
    re.compile(
        r"\bnot\s+(?:eligible|available|open)\s+for\s+(?:visa\s+|immigration\s+)?sponsorship"
    ),
    re.compile(
        r"\bwithout\s+(?:the\s+need\s+for\s+|requiring\s+|need\s+of\s+)?(?:visa\s+|employer\s+|immigration\s+)?sponsorship"
    ),
    re.compile(
        r"\b(?:no|non-?)\s*(?:visa\s+|immigration\s+|employer\s+)?sponsorship\b"
    ),
    re.compile(r"\bsponsorship\s+(?:is\s+)?not\s+(?:offered|available|provided)"),
)
_MUST_BE_AUTHORIZED_RE = re.compile(
    r"\bmust\s+(?:be\s+|currently\s+be\s+|already\s+be\s+)?(?:legally\s+)?(?:authori[sz]ed|eligible|permitted)\s+to\s+work"
    r"|\bauthori[sz]ation\s+to\s+work\s+(?:in\s+the\s+\w+\s+)?(?:is\s+)?required"
)
_CITIZENSHIP_REQUIRED_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bu\.?s\.?\s+citizen(?:ship)?\s+(?:is\s+)?(?:required|mandatory)"),
    re.compile(
        r"\bmust\s+be\s+(?:an?\s+)?(?:u\.?s\.?|united states|american)\s+citizen"
    ),
    re.compile(r"\b(?:u\.?s\.?|united states)\s+citizens\s+only\b"),
    re.compile(r"\bcitizenship\s+(?:is\s+)?required"),
    re.compile(r"\brequires?\s+(?:u\.?s\.?|united states)\s+citizenship"),
)
_CLEARANCE_REQUIRED_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:active\s+)?(?:security\s+)?clearance\s+(?:is\s+)?(?:required|mandatory)"
    ),
    re.compile(
        r"\bmust\s+(?:hold|have|possess|maintain)\s+(?:an?\s+)?(?:active\s+|current\s+)?(?:\w+[\s/-]+){0,2}clearance"
    ),
    re.compile(r"\b(?:top secret|ts/sci|secret|public trust)\s+clearance\b"),
    re.compile(r"\brequires?\s+(?:an?\s+)?(?:active\s+)?(?:\w+\s+){0,2}clearance"),
)
_OFFERS_SPONSORSHIP_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:visa\s+|immigration\s+)?sponsorship\s+(?:is\s+|will\s+be\s+|may\s+be\s+)?(?:available|offered|provided|possible)"
    ),
    re.compile(
        r"\b(?:we|company|employer)\s+(?:will|can|are able to|are willing to|are happy to"
        r"|are open to|is willing to|is able to|do|offer)\s+(?:\w+\s+){0,2}?sponsor"
    ),
    re.compile(r"\bwill\s+sponsor\b"),
    re.compile(r"\bopen\s+to\s+(?:visa\s+)?sponsor"),
)


def _any_match(patterns: Sequence[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match is not None:
            return match.group(0)
    return None


def _work_authorization_dimension(
    weight: float, job: Mapping[str, Any], profile: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    name = "work_authorization"
    text = _normalize_text(
        f"{job.get('sponsorship_statement') or ''}\n{job.get('description') or ''}"
    )
    authorization = _normalize_text(profile.get("work_authorization"))
    sponsorship = _normalize_text(profile.get("sponsorship_required"))
    needs_sponsorship = authorization == "requires_sponsorship" or sponsorship == "yes"
    authorized = authorization == "authorized" and sponsorship in ("", "no")
    blockers: list[str] = []

    no_sponsorship = _any_match(_NO_SPONSORSHIP_RES, text)
    if no_sponsorship is not None:
        evidence = [
            {"fact": f'The posting states: "{no_sponsorship}".', "source": "job"}
        ]
        if needs_sponsorship:
            blocker = "The posting states it does not offer visa sponsorship, and your profile says you need sponsorship."
            blockers.append(blocker)
            evidence.append(
                {"fact": "Your profile says you need sponsorship.", "source": "profile"}
            )
            return _dimension(
                name,
                weight,
                score=0.0,
                reason=blocker,
                evidence=evidence,
                missing=["sponsorship"],
            ), blockers
        if authorized:
            evidence.append(
                {
                    "fact": "Your profile says you are authorized to work without sponsorship.",
                    "source": "profile",
                }
            )
            return _dimension(
                name,
                weight,
                score=100.0,
                reason="The posting does not sponsor and your profile says you are authorized without sponsorship.",
                evidence=evidence,
                matched=["authorized"],
            ), blockers
        message = "The posting states it does not offer sponsorship; add your work-authorization status (and whether you need sponsorship) to assess it."
        return _dimension(
            name,
            weight,
            score=None,
            reason=message,
            evidence=evidence,
            missing_information=message,
        ), blockers

    citizenship_required = _any_match(_CITIZENSHIP_REQUIRED_RES, text)
    clearance_required = _any_match(_CLEARANCE_REQUIRED_RES, text)
    if citizenship_required is not None or clearance_required is not None:
        statement = citizenship_required or clearance_required or ""
        evidence = [{"fact": f'The posting states: "{statement}".', "source": "job"}]
        if citizenship_required is not None:
            citizenship = _normalize_text(profile.get("citizenship"))
            if not citizenship:
                message = "The posting requires US citizenship; your profile does not state citizenship, so this was not scored (never assumed)."
                return _dimension(
                    name,
                    weight,
                    score=None,
                    reason=message,
                    evidence=evidence,
                    missing_information=message,
                ), blockers
            is_us = citizenship in {
                "us",
                "usa",
                "u.s.",
                "u.s.a.",
                "united states",
                "us citizen",
                "american",
                "united states of america",
            }
            evidence.append(
                {
                    "fact": f"Your profile lists citizenship '{citizenship}'.",
                    "source": "profile",
                }
            )
            if not is_us:
                blocker = "The posting requires US citizenship, and your profile lists a different citizenship."
                blockers.append(blocker)
                return _dimension(
                    name,
                    weight,
                    score=0.0,
                    reason=blocker,
                    evidence=evidence,
                    missing=["us citizenship"],
                ), blockers
            if clearance_required is None:
                return _dimension(
                    name,
                    weight,
                    score=100.0,
                    reason="The posting requires US citizenship and your profile states US citizenship.",
                    evidence=evidence,
                    matched=["us citizenship"],
                ), blockers
        clearance = _normalize_text(profile.get("security_clearance"))
        if not clearance:
            message = "The posting requires a security clearance; your profile does not state one, so this was not scored (never assumed)."
            return _dimension(
                name,
                weight,
                score=None,
                reason=message,
                evidence=evidence,
                missing_information=message,
            ), blockers
        evidence.append(
            {
                "fact": f"Your profile lists security clearance '{clearance}'.",
                "source": "profile",
            }
        )
        if clearance in {"none", "no", "n/a", "not applicable", "false"}:
            blocker = "The posting requires a security clearance, and your profile states you do not hold one."
            blockers.append(blocker)
            return _dimension(
                name,
                weight,
                score=0.0,
                reason=blocker,
                evidence=evidence,
                missing=["security clearance"],
            ), blockers
        return _dimension(
            name,
            weight,
            score=100.0,
            reason="The posting requires a clearance and your profile states one.",
            evidence=evidence,
            matched=["security clearance"],
        ), blockers

    must_be_authorized = _MUST_BE_AUTHORIZED_RE.search(text)
    if must_be_authorized is not None:
        evidence = [
            {
                "fact": f'The posting states: "{must_be_authorized.group(0)}".',
                "source": "job",
            }
        ]
        if authorization == "authorized":
            evidence.append(
                {
                    "fact": "Your profile says you are authorized to work.",
                    "source": "profile",
                }
            )
            return _dimension(
                name,
                weight,
                score=100.0,
                reason="The posting requires existing work authorization and your profile says you are authorized.",
                evidence=evidence,
                matched=["authorized"],
            ), blockers
        if authorization == "requires_sponsorship":
            blocker = "The posting requires existing work authorization, and your profile says you need sponsorship."
            blockers.append(blocker)
            evidence.append(
                {"fact": "Your profile says you need sponsorship.", "source": "profile"}
            )
            return _dimension(
                name,
                weight,
                score=0.0,
                reason=blocker,
                evidence=evidence,
                missing=["authorized"],
            ), blockers
        message = "The posting requires existing work authorization; add your work-authorization status to assess it."
        return _dimension(
            name,
            weight,
            score=None,
            reason=message,
            evidence=evidence,
            missing_information=message,
        ), blockers

    offers = _any_match(_OFFERS_SPONSORSHIP_RES, text)
    if offers is not None:
        message = "The posting states sponsorship is available, so no work-authorization restriction is scored."
        return _dimension(
            name,
            weight,
            score=None,
            reason=message,
            evidence=[{"fact": f'The posting states: "{offers}".', "source": "job"}],
            missing_information=message,
        ), blockers
    message = "The posting does not state a restriction."
    return _dimension(
        name, weight, score=None, reason=message, missing_information=message
    ), blockers


def _resume_coverage_dimension(
    weight: float, resume_text: str, job: Mapping[str, Any], profile: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    name = "resume_coverage"
    coverage = score_keyword_evidence(
        resume_text,
        str(job.get("title") or ""),
        str(job.get("description") or ""),
        preferences={
            "verified_skills": _str_list(profile.get("verified_skills")),
            "locations": _str_list(profile.get("locations")),
            "workplace_types": _str_list(profile.get("workplace_types")),
        },
        job_location=str(job.get("location") or ""),
        workplace_type=str(job.get("workplace_type") or "") or None,
    )
    score = coverage.get("score")
    if score is None:
        message = (
            " ".join(coverage.get("missing_inputs") or [])
            or "Resume keyword coverage could not be computed."
        )
        return _unscored(name, weight, message), coverage
    matched: list[str] = []
    evidence: list[dict[str, str]] = []
    for dimension in coverage.get("dimensions", []):
        for item in dimension.get("matched_evidence", []) or []:
            term = str(item.get("term"))
            if term in matched:
                continue
            matched.append(term)
            source = (
                "verified_skill"
                if item.get("source") == "verified_candidate_skill"
                else "resume"
            )
            evidence.append(
                {
                    "fact": f"Posting term '{term}' appears in your {'verified skills' if source == 'verified_skill' else 'resume'}.",
                    "source": source,
                }
            )
    missing = sorted(
        {
            str(term)
            for dimension in coverage.get("dimensions", [])
            for term in dimension.get("missing_terms", []) or []
        }
    )
    return (
        _dimension(
            name,
            weight,
            score=float(score),
            reason=f"Exact-term coverage ({coverage.get('version')}) scored {float(score):.2f}; this is lexical, not semantic, evidence.",
            evidence=evidence[:30],
            matched=matched[:30],
            missing=missing[:30],
        ),
        coverage,
    )


_FINGERPRINT_PROFILE_KEYS = (
    "verified_skills",
    "years_of_experience",
    "target_seniority",
    "career_profile",
    "locations",
    "workplace_types",
    "salary_expectation_min",
    "salary_expectation_max",
    "currency",
    "work_authorization",
    "sponsorship_required",
    "relocation",
    "citizenship",
    "security_clearance",
)
_FINGERPRINT_JOB_KEYS = (
    "title",
    "description",
    "location",
    "workplace_type",
    "salary_min",
    "salary_max",
    "currency",
    "required_skills",
    "preferred_skills",
    "seniority",
    "sponsorship_statement",
)


def _fingerprint_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _fingerprint_value(item)
            for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, Sequence):
        return [_fingerprint_value(item) for item in value]
    return str(value)


def _inputs_fingerprint(
    resume_text: str, profile: Mapping[str, Any], job: Mapping[str, Any]
) -> str:
    payload = {
        "resume_text": resume_text,
        "profile": {
            key: _fingerprint_value(profile.get(key))
            for key in _FINGERPRINT_PROFILE_KEYS
        },
        "job": {key: _fingerprint_value(job.get(key)) for key in _FINGERPRINT_JOB_KEYS},
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def score_job(
    *,
    profile: Mapping[str, Any],
    resume_text: str,
    job: Mapping[str, Any],
    weights: ScoringWeights | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Score one job against a candidate using only explicit evidence."""
    weights = weights or ScoringWeights()
    weight_map = weights.as_dict()
    resume_text = str(resume_text or "")
    resume_norm = _normalize_text(resume_text)
    description = str(job.get("description") or "")
    verified_raw = _str_list(profile.get("verified_skills"))
    verified_norm_list = [_normalize_text(item) for item in verified_raw]
    verified_norm_text = " ; ".join(verified_norm_list)
    candidate = _candidate_skills(profile, resume_norm)

    job_required = [
        skill
        for raw in _str_list(job.get("required_skills"))
        for skill in _canonicalize_skill(raw)
    ]
    job_preferred = [
        skill
        for raw in _str_list(job.get("preferred_skills"))
        for skill in _canonicalize_skill(raw)
    ]
    if not job_required and not job_preferred:
        extracted_required, extracted_preferred = extract_job_skills(description)
        job_required, job_preferred = (
            list(extracted_required),
            list(extracted_preferred),
        )
    elif not job_required:
        job_required = list(extract_job_skills(description)[0])
    job_required = list(dict.fromkeys(job_required))
    job_preferred = [
        skill for skill in dict.fromkeys(job_preferred) if skill not in job_required
    ]

    dimensions: list[dict[str, Any]] = []
    dimensions.append(
        _skills_dimension(
            "required_skills",
            weight_map["required_skills"],
            job_required,
            candidate,
            resume_norm,
            verified_norm_list,
        )
    )
    dimensions.append(
        _skills_dimension(
            "preferred_skills",
            weight_map["preferred_skills"],
            job_preferred,
            candidate,
            resume_norm,
            verified_norm_list,
        )
    )
    dimensions.append(
        _experience_dimension(weight_map["experience"], description, profile)
    )
    dimensions.append(_seniority_dimension(weight_map["seniority"], job, profile))
    dimensions.append(
        _leadership_dimension(
            weight_map["leadership_architecture"],
            description,
            profile,
            resume_norm,
            verified_norm_text,
        )
    )
    dimensions.append(_location_dimension(weight_map["location"], job, profile))
    dimensions.append(_workplace_dimension(weight_map["workplace"], job, profile))
    dimensions.append(_salary_dimension(weight_map["salary"], job, profile))
    authorization_dimension, hard_blockers = _work_authorization_dimension(
        weight_map["work_authorization"], job, profile
    )
    dimensions.append(authorization_dimension)
    coverage_dimension, coverage = _resume_coverage_dimension(
        weight_map["resume_coverage"], resume_text, job, profile
    )
    dimensions.append(coverage_dimension)

    weighted_total = 0.0
    available_weight = 0.0
    for dimension in dimensions:
        if dimension["score"] is None:
            continue
        weighted_total += float(dimension["score"]) * float(dimension["weight"])
        available_weight += float(dimension["weight"])
    score = (
        round(weighted_total / available_weight, 2) if available_weight > 0 else None
    )
    label, reason = classify(score, hard_blockers=hard_blockers)

    missing_inputs: list[str] = []
    if not resume_norm:
        missing_inputs.append("No resume text was provided.")
    if not description.strip() and not str(job.get("title") or "").strip():
        missing_inputs.append("No job title or description was provided.")
    for dimension in dimensions:
        message = dimension.get("missing_information")
        if dimension["score"] is None and message and message not in missing_inputs:
            missing_inputs.append(message)

    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return {
        "version": SCORING_VERSION,
        "score": score,
        "classification": label,
        "classification_reason": reason,
        "weights": weight_map,
        "dimensions": dimensions,
        "hard_blockers": list(hard_blockers),
        "missing_inputs": missing_inputs,
        "resume_coverage": coverage,
        "computed_at": moment.astimezone(timezone.utc).isoformat(),
        "inputs_fingerprint": _inputs_fingerprint(resume_text, profile, job),
    }
