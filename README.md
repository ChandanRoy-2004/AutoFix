# 🤖 AutoFix: Enterprise Multi-Agent Code Healer

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg?style=for-the-badge&logo=python)](https://www.python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Modern-009688.svg?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![Gemini AI](https://img.shields.io/badge/AI-Google_Gemini-orange.svg?style=for-the-badge&logo=google)](https://ai.google.dev/)
[![TailwindCSS](https://img.shields.io/badge/Tailwind-CSS-38B2AC.svg?style=for-the-badge&logo=tailwind-css)](https://tailwindcss.com/)

> **Write code. Break it. Let the AI agents fix it.**  
> An automated CI/CD debugging pipeline where specialized AI agents write tests, execute them in a secure sandbox, analyze the crash logs, and patch the source code autonomously.

---

## 🌐 Live Application

<div align="center">

[![Live Demo](https://img.shields.io/badge/▶_LAUNCH_LIVE_DEMO-00E676?style=for-the-badge&logo=googlechrome&logoColor=white&labelColor=1a1a1a)](https://your-deployment-link.com)

👉 **[Experience AutoFix Live in Your Browser](https://your-deployment-link.com)** 👈

*No installation required. Paste broken code and watch the self-healing multi-agent loop resolve defects in real time.*

</div>

---

## 🎯 The Mission
In modern software development, the gap between Quality Assurance testing and Software Engineering often slows down delivery. **AutoFix** bridges this exact gap. 

By upgrading the traditional automated testing mindset (like Selenium or TestNG) with modern LLM orchestration, this tool acts as an autonomous QA-to-Developer pipeline. It proves that testing doesn't just have to *find* bugs—it can actively *resolve* them.

---

## 🏛️ System Architecture

AutoFix uses a **Layered Service Architecture**, separating HTTP routing, data validation, pure business logic, and AI prompt engineering.

```mermaid
flowchart LR
    %% Direction: Left to Right for Horizontal Readability
    
    subgraph Ingestion ["1. Event Ingestion"]
        direction TB
        PR["🔀 PR Event<br><i>opened or sync</i>"] -->|Webhook Payload| GH_App["⚡ GitHub App"]
        GH_App -->|POST Event| Endpoint["🌐 /api/github/webhook"]
        Endpoint --> AuthCheck{"🔐 HMAC Check"}
        AuthCheck -->|Valid| Accept["✅ 202 Accepted"]
        AuthCheck -->|Invalid| Reject["❌ 401 Unauthorized"]
    end

    subgraph PreFlight ["2. Pre-Flight Check"]
        direction TB
        Accept -->|Trigger| BGTask["⚙️ Background Task"]
        BGTask --> GitClone["📦 Clone PR Branch"]
        GitClone --> RunPytest["🧪 Run pytest"]
        RunPytest --> CheckStatus{"🔍 Tests Passed?"}
        CheckStatus -->|Yes| CleanExit["🟢 Healthy Exit"]
    end

    subgraph HealingLoop ["3. Gemini Healing Loop"]
        direction TB
        CheckStatus -->|No| Extract["📑 Extract Trace"]
        Extract --> Prompt["📝 Construct Prompt"]
        Prompt --> Gemini["🤖 Gemini AI"]
        Gemini --> Patch["💡 Generate Patch"]
    end

    subgraph Verification ["4. Closed-Loop Verification"]
        direction TB
        Patch --> Apply["🛠️ Apply Patch"]
        Apply --> ReTest["🔄 Re-test & AST Check"]
        ReTest --> ReTestStatus{"🔍 Tests Passed?"}
        ReTestStatus -->|Retry| Gemini
    end

    subgraph PRFeedback ["5. PR Feedback"]
        direction TB
        ReTestStatus -->|Yes| Push["🚀 Commit & Push Fix"]
        Push --> Comment["💬 PR Audit Comment"]
    end

    %% Color Styling & Themes
    classDef default fill:#1f2937,stroke:#374151,color:#f3f4f6;
    classDef ingestion fill:#1e3a8a,stroke:#3b82f6,color:#eff6ff;
    classDef security fill:#78350f,stroke:#d97706,color:#fef3c7;
    classDef execution fill:#0f766e,stroke:#14b8a6,color:#ccfbf1;
    classDef ai fill:#581c87,stroke:#a855f7,color:#f3e8ff;
    classDef success fill:#065f46,stroke:#10b981,color:#ecfdf5;
    classDef failure fill:#991b1b,stroke:#ef4444,color:#fef2f2;

    class PR,GH_App,Endpoint ingestion;
    class AuthCheck,Accept security;
    class BGTask,GitClone,RunPytest,CheckStatus,CleanExit execution;
    class Extract,Prompt,Gemini,Patch ai;
    class Apply,ReTest,ReTestStatus,Push,Comment success;
    class Reject failure;
```

### Directory Structure
```text
autofix-pipeline/
├── .env                     # API Keys & Secrets
├── requirements.txt         # Project Dependencies
├── pro.png                  # Architecture Diagram 
├── workspace/               # Local Execution Sandbox
└── app/                     
    ├── main.py              # Application Entry Point
    ├── api/                 # REST Endpoints
    ├── core/                # Configuration & System Prompts
    ├── models/              # Pydantic Validation Schemas
    ├── services/            # Sandbox & CI/CD Orchestration
    ├── agents/              # AI Persona Logic
    └── static/              # HTML/JS/CSS Frontend