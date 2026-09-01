# Automated AI Code Review Platform Using Static Analysis Tooling and LLM Capabilities

## The Team
1. Aaliyah Umoru
2. Moses Uzodinma
3. Ademola Kolawole
4. Jalen Davis

## Problem Statement
In Software Engineering and other development environments, code review is regarded as a principal aspect of it and is commonly integrated into version control systems such as GitHub and Microsoft Azure DevOps. However, this process, when done manually, can be extremely daunting and time-consuming, and can create a dependence on certain qualified individuals with review expertise. Additionally, AI-generated code is now being used in codebases for software development tasks, and most times, this code is error-prone and contains subtle issues such as code smells, vulnerabilities, etc. This automated code review platform can serve as a resource, assistant, and aid in terms of review, and assist skilled reviewers in their reviewing tasks, help developers in the absence of a human reviewer, and most importantly, ensure every code pushed to a production environment has very minimal risk of breaking post-deployment.

## Target Users
Vibe-Coders, Programmers, Software Developers, Code Authors, Code Reviewers, Junior Developers, Security Engineers.

## Main Features (In Priority Order)
1. **Diff/Patch/PR Ingestion** — Accept code changes from the user through file upload, pasted patch text, or a Git repository URL with a commit or branch reference for review.
2. **Git Repository Handling** — Clone or fetch a repository, check out the relevant commit or branch, and extract the file tree needed for analysis.
3. **Diff Parsing** — Convert raw diff or patch text into a structured format, including changed files and line numbers, that downstream modules such as linters and LLM-based analysis can process. The parser will also identify modified files and lines.
4. **Security Checks** — Run static analysis and security checks against the changed code, including credential detection and redaction.
5. **Input Validation and Sanitization** — Reject malformed diffs, oversized submissions, and disallowed file types before they reach the analysis stage.
6. **Secure Submission Handling** — Isolate or sandbox untrusted code so that parsing and analysis never execute submitted code directly.

## Functional Requirements
1. The system shall accept a `.diff`/`.patch` file upload and parse it into a list of changed files with line-level detail.
2. The system shall accept a public Git repository URL and a branch or commit hash, and successfully clone it locally.
3. The system shall reject any submission exceeding a defined size limit (e.g., 10 MB) and return a clear error message.
4. The system shall detect and reject malformed or non-diff-format input, returning a descriptive validation error.
5. The system shall output parsed diff data as structured JSON containing file paths, hunks, and line numbers, ready for hand-off to the static analysis and LLM modules.

## Non-Functional Requirements
1. **Performance** — Diff parsing shall complete within 5 seconds for files up to 5,000 changed lines.
2. **Security** — Submitted code shall never be executed; all parsing shall run in a sandboxed/isolated process with no filesystem access outside a designated temp directory.
3. **Reliability** — The pipeline shall handle at least 20 concurrent submissions without data loss or crash.

## Major Development Tasks
1. Build the input interface: file upload + repo URL/branch form.
2. Implement the unified diff parser (file, hunk, line-level extraction).
3. Implement Git clone/fetch logic with basic auth/token support for private repos.
4. Build validation & sanitization layer (size limits, format checks, path traversal protection).
5. Define the JSON output schema and write the hand-off interface to Student 2 and Student 3's modules.
