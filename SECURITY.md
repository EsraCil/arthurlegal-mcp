# Security policy

ArthurLegal MCP is a read-only legal research endpoint. Lawyers send it search queries that can contain details
of their matters, so we treat anything that exposes one caller's queries or results to someone else, lets a
caller reach hosts or files the server should not touch, or runs code on the server as a security issue.

## Reporting a vulnerability

Please do **not** open a public issue. Email **ertug@datsproject.io** with:

- what you found and where (file, tool name or URL),
- how to reproduce it (a request or a short script),
- what an attacker gains.

We confirm receipt, keep you informed while we fix it, and credit you in the release notes unless you prefer
otherwise. Please give us a reasonable time to ship a fix before you disclose.

## Scope

- In scope: the gateway (`server.py`, `mcpcore.py`, `denetim.py`), every backend folder in this repository, the
  desk interface in `arayuz/` and the deployed endpoint `https://arthurlegal-mcp.fly.dev/mcp`.
- Out of scope: the public government and court websites the tools read from, and denial of service by sending
  large volumes of traffic.

The threat model the maintainers work from is in [`.oss-scanner/threat_model.md`](.oss-scanner/threat_model.md).

---

## Güvenlik politikası (Türkçe)

Bir açık bulduysanız lütfen herkese açık issue açmayın; **ertug@datsproject.io** adresine ne bulduğunuzu, nasıl
tekrarlanacağını ve saldırganın ne elde ettiğini yazın. Aldığımızı bildirir, düzeltme sürecinde sizi bilgilendirir
ve aksini istemezseniz sürüm notlarında adınızı anarız. Kapsam ve tehdit modeli yukarıda.
