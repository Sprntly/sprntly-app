import type { LegalSection } from "../components/legal/LegalDocument"

export const TERMS_EFFECTIVE = "May 20, 2026"
export const TERMS_LAST_UPDATED = "May 20, 2026"

export const TERMS_SECTIONS: LegalSection[] = [
  {
    id: "intro",
    title: "",
    blocks: [
      'These Terms of Use (the "Terms") govern your access to and use of the websites, products, and services provided by Sprntly, Inc. ("Sprntly," "we," "us," or "our"), including sprntly.ai and the Sprntly Execution Intelligence platform (collectively, the "Service").',
      "If you are entering into these Terms on behalf of a company or other legal entity, you represent that you have the authority to bind that entity, and \"you\" and \"Customer\" refer to that entity. The Service is intended for use by businesses and their authorized personnel; it is not intended for personal or consumer use.",
      'Order of precedence. If you and Sprntly have entered into a separate written agreement covering your use of the Service — for example, a Master Services Agreement, Order Form, Enterprise Agreement, or Data Processing Addendum (each, a "Customer Agreement") — that Customer Agreement controls in the event of any conflict with these Terms. Otherwise, these Terms govern.',
    ],
  },
  {
    id: "service",
    title: "1. The Service",
    blocks: [
      "Sprntly provides an AI-powered Execution Intelligence platform for enterprise product and engineering organizations. The Service connects to data sources you authorize, continuously analyzes signals across those sources, and generates outputs such as priority recommendations, intelligence briefs, product requirements documents (PRDs), prototypes, and related artifacts to help your teams decide what to build and ship.",
      "We may improve, update, or modify the Service over time. We will not materially reduce the core functionality of a paid Service tier during your then-current subscription term without the notice required in your Customer Agreement.",
    ],
  },
  {
    id: "eligibility",
    title: "2. Eligibility and authorized users",
    blocks: [
      "The Service is for business use only. To use the Service you must be (a) at least 18 years old, (b) authorized by your organization to access the Service on its behalf, and (c) capable of forming a legally binding contract.",
      'You may authorize employees and contractors of your organization who are bound by confidentiality and acceptable use obligations no less protective than these Terms to access the Service as "Authorized Users." You are responsible for the acts and omissions of your Authorized Users, including any breach of these Terms.',
    ],
  },
  {
    id: "accounts",
    title: "3. Accounts and security",
    blocks: [
      "To use the Service you must create an account. You agree to: provide accurate, current, and complete account information; safeguard your credentials, API keys, and any tokens used to authenticate to the Service; restrict Authorized User access to those with a legitimate business need; promptly deactivate access for Authorized Users who no longer require it; and promptly notify us at security@sprntly.ai of any suspected unauthorized access to your account.",
      "You are responsible for all activity that occurs under your account, including the activity of your Authorized Users.",
    ],
  },
  {
    id: "subscriptions",
    title: "4. Subscriptions, fees, and payment",
    blocks: [
      "Subscriptions. Access to paid features of the Service is governed by the subscription tier, scope, term, and fees set out in your Order Form, Customer Agreement, or, for self-service subscriptions, the terms presented at purchase.",
      "Fees and payment. You agree to pay all fees applicable to your subscription. Unless otherwise specified, fees are billed in advance, are non-cancellable, and are non-refundable. Late payments accrue interest at the lower of 1.0% per month or the maximum rate permitted by law. We may suspend the Service for non-payment after providing the notice required in your Customer Agreement (or, for self-service subscriptions, ten (10) business days' notice).",
      "Taxes. All fees are exclusive of sales, use, value-added, withholding, and similar taxes. You are responsible for all such taxes other than taxes on our net income.",
      "Renewals and changes. Subscriptions automatically renew for successive terms of equal length unless either party gives notice of non-renewal as specified in your Customer Agreement (or, for self-service subscriptions, by cancelling auto-renewal in the Service before the renewal date). We may change pricing at renewal with notice; price changes do not take effect mid-term.",
    ],
  },
  {
    id: "customer-data",
    title: "5. Customer Data",
    blocks: [
      "5.1 Definitions and ownership. \"Customer Data\" means data, content, files, and information that (a) you or your Authorized Users submit to the Service, or (b) the Service ingests from third-party systems you connect to it on your behalf — including but not limited to product analytics, support tickets, sales call transcripts, user research, customer feedback, telemetry, code repositories, design files, and documents from connected workspaces (such as Google Drive). As between you and Sprntly, you retain all right, title, and interest in and to your Customer Data. Nothing in these Terms transfers ownership of Customer Data to us.",
      "5.2 License to Sprntly. You grant Sprntly a worldwide, non-exclusive, royalty-free license to access, use, store, process, transmit, display, and create derivative works of Customer Data solely as necessary to: provide, secure, monitor, support, and improve the Service for you; generate the Outputs (as defined below) that the Service is designed to produce; comply with applicable law and lawful requests of governmental authorities; and exercise our rights and perform our obligations under these Terms and any Customer Agreement.",
      "5.3 No training of third-party foundation models. We do not, and we do not permit our AI sub-processors to, use Customer Data to train, fine-tune, or otherwise improve any third-party foundation model or any model that is made available to other Sprntly customers or to the public. Where the Service uses third-party large language model or AI infrastructure providers, we contract with them on terms that prohibit such use. We may use Customer Data internally to operate, secure, debug, support, and improve the Service for you, including to fine-tune or evaluate Sprntly-controlled models that are deployed exclusively to your tenant or environment, except as otherwise specified in your Customer Agreement.",
      '5.4 Aggregated and de-identified data. We may generate aggregated, anonymized, and de-identified data from Customer Data ("Aggregated Data") that does not identify you, any Authorized User, any individual, or any other Sprntly customer. We may use Aggregated Data to operate, secure, and improve the Service, develop new features, conduct benchmarking and research, and publish industry-level statistics. Aggregated Data is not Customer Data.',
      '5.5 Your representations regarding Customer Data. You represent and warrant that: you have all rights, consents, and authority necessary to provide Customer Data to the Service and to grant the license in Section 5.2; your Customer Data, and our processing of it as authorized by you, will not violate applicable law or infringe or misappropriate any third party\'s rights; you have provided all notices and obtained all consents required by applicable data protection and privacy laws from individuals whose personal information is included in Customer Data; you have made and will maintain backups of Customer Data sufficient to meet your operational needs; and you will not submit Restricted Data (including HIPAA-regulated PHI, PCI cardholder data, information of minors under 16, classified or export-controlled information, or regulated biometric identifiers) unless we have specifically agreed in writing in your Customer Agreement. You are solely responsible for the accuracy, completeness, lawfulness, and quality of Customer Data.',
    ],
  },
  {
    id: "outputs",
    title: "6. Outputs",
    blocks: [
      '6.1 What Outputs are. "Outputs" means the briefs, recommendations, priorities, PRDs, prototypes, summaries, analyses, code, and other artifacts generated by the Service from Customer Data and the Service\'s underlying models.',
      "6.2 Ownership of Outputs. Subject to your payment of fees and compliance with these Terms, as between you and Sprntly, you own all right, title, and interest in and to Outputs generated for your account. We assign to you all rights we may have in such Outputs to the maximum extent permitted by applicable law.",
      "6.3 Limitations on Output ownership. You acknowledge that: similar or identical Outputs may be generated for other customers, and you do not have exclusive rights to ideas, methods, or patterns reflected in Outputs; Outputs may incorporate or be informed by the Service's underlying models, prompts, training data, and methodology, all of which remain our property; and you are responsible for reviewing and validating any Output before relying on it, making any business decision based on it, or sharing it externally.",
      "6.4 No professional advice; nature of AI Outputs. Outputs are generated by AI systems and may contain errors, omissions, biases, or content that is incomplete, out of date, or inaccurate. Outputs are provided to inform and accelerate the judgment of qualified human reviewers and do not constitute professional, legal, financial, medical, engineering, safety, regulatory, or any other form of advice. You must not use Outputs as the sole basis for decisions that materially affect the legal rights or safety of any person, require professional licensure, or require compliance with obligations that mandate human review. You are solely responsible for the decisions you make based on Outputs.",
    ],
  },
  {
    id: "connectors",
    title: "7. Connectors and third-party services",
    blocks: [
      'The Service integrates with third-party systems and data sources that you authorize ("Connectors") — for example, analytics platforms, CRMs, support systems, code hosts, design tools, Google Drive, Google Analytics, Gmail, and similar services. When you authorize a Connector: you authorize us to access, retrieve, store, and process the data the Connector exposes, on your behalf; you are responsible for ensuring you have the right to grant us such access and that doing so does not violate the third party\'s terms or any applicable agreement; the operation of the Connector depends on the third party\'s systems and terms, which are outside our control; we are not responsible for changes, errors, downtime, or data quality issues caused by third parties; and the third party may charge you fees, impose rate limits, or restrict access on its own terms.',
      "We may modify or discontinue Connectors at any time, including in response to changes by the third party.",
    ],
  },
  {
    id: "acceptable-use",
    title: "8. Acceptable use",
    blocks: [
      "You and your Authorized Users will not, and will not permit any third party to: use the Service in violation of applicable law or in a manner that infringes or violates third-party rights; use the Service to develop, train, evaluate, or benchmark any competing AI product or service; reverse engineer, decompile, disassemble, or attempt to extract the weights, prompts, system instructions, or underlying source code of the Service; circumvent access controls or usage limits; introduce malware or conduct security testing without our prior written consent; scrape or harvest data from the Service except through documented APIs; use the Service to generate unlawful, harmful, or deceptive content; use the Service for automated decisions that significantly affect individuals without appropriate human oversight; use the Service in connection with weapons systems, unlawful surveillance, or activities prohibited by export controls; or attempt to re-identify de-identified or Aggregated Data.",
      "We may suspend access immediately, without prior notice, if we reasonably determine that your use materially violates this Section 8, threatens the security or integrity of the Service, or exposes us to legal or regulatory risk.",
    ],
  },
  {
    id: "confidentiality",
    title: "9. Confidentiality",
    blocks: [
      'Each party may have access to non-public information of the other party ("Confidential Information"). The Receiving Party will use Confidential Information only as necessary to exercise its rights and perform its obligations, and protect it using at least a reasonable degree of care. Confidential Information does not include information that is publicly available without breach, independently developed, or rightfully received from a third party without confidentiality obligations. The Receiving Party may disclose Confidential Information when required by law, provided that (where legally permitted) it gives reasonable advance notice and cooperates in seeking confidential treatment.',
    ],
  },
  {
    id: "ip",
    title: "10. Intellectual property",
    blocks: [
      "10.1 Sprntly's intellectual property. The Service, including all software, models, prompts, system instructions, methodologies, templates, user interfaces, and documentation, is and remains the property of Sprntly and its licensors. We grant you a non-exclusive, non-transferable, non-sublicensable right to access and use the Service during your subscription term, solely as described in these Terms and any Customer Agreement.",
      '10.2 Feedback. If you provide suggestions or other feedback ("Feedback"), you grant us a perpetual, irrevocable, royalty-free license to use the Feedback for any purpose, without restriction or attribution.',
      "10.3 Trademarks. Neither party may use the other's name, logos, or trademarks without prior written consent, except as permitted in your Customer Agreement.",
    ],
  },
  {
    id: "privacy",
    title: "11. Privacy and data protection",
    blocks: [
      "Our processing of personal information is described in our Privacy Policy. If your use of the Service involves our processing of personal information on your behalf, our Data Processing Addendum (the \"DPA\") applies and is incorporated into these Terms by reference. Where the DPA conflicts with these Terms with respect to personal information, the DPA controls.",
    ],
  },
  {
    id: "security",
    title: "12. Security",
    blocks: [
      "We implement administrative, technical, and physical safeguards designed to protect Customer Data against unauthorized access, alteration, disclosure, or destruction. The current description of our security program is available on request and, for enterprise customers, may be supplemented by additional commitments in your Customer Agreement. No security program is perfect, and we cannot guarantee that Customer Data will never be subject to unauthorized access.",
    ],
  },
  {
    id: "term",
    title: "13. Term, suspension, and termination",
    blocks: [
      "Term. These Terms remain in effect for as long as you use the Service, or as set forth in your Customer Agreement.",
      "Termination for convenience. For self-service subscriptions, you may cancel at any time through the Service; cancellation takes effect at the end of the then-current billing period.",
      "Termination for cause. Either party may terminate for the other party's material breach if not cured within thirty (30) days after written notice. We may terminate immediately on notice if you breach Sections 5.5, 8, or 9, or if continued provision would violate applicable law.",
      "Effect of termination. On termination: your right to access the Service ends; we will delete or return Customer Data per your Customer Agreement, DPA, and applicable law; and surviving Sections (including 5.5, 6, 9, 10, 14–18) continue in effect.",
    ],
  },
  {
    id: "disclaimers",
    title: "14. Disclaimers",
    blocks: [
      'THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE." TO THE MAXIMUM EXTENT PERMITTED BY LAW, SPRNTLY DISCLAIMS ALL WARRANTIES, EXPRESS, IMPLIED, OR STATUTORY, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE, ACCURACY, AND NON-INFRINGEMENT.',
      "WITHOUT LIMITING THE FOREGOING, SPRNTLY MAKES NO WARRANTY THAT THE SERVICE WILL BE UNINTERRUPTED, ERROR-FREE, OR SECURE; THAT OUTPUTS WILL BE ACCURATE, COMPLETE, CURRENT, OR FIT FOR ANY PARTICULAR USE; OR THAT CONNECTORS WILL CONTINUE TO BE AVAILABLE OR FUNCTION AS THEY HAVE IN THE PAST.",
      "YOU ACKNOWLEDGE THAT OUTPUTS ARE GENERATED BY AI SYSTEMS AND MAY CONTAIN ERRORS, AND THAT YOU ARE RESPONSIBLE FOR EVALUATING THEM BEFORE RELYING ON THEM.",
    ],
  },
  {
    id: "liability",
    title: "15. Limitation of liability",
    blocks: [
      "TO THE MAXIMUM EXTENT PERMITTED BY LAW, IN NO EVENT WILL SPRNTLY BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE DAMAGES, OR ANY LOSS OF PROFITS, REVENUE, DATA, USE, GOODWILL, OR OTHER INTANGIBLE LOSSES, ARISING OUT OF OR IN CONNECTION WITH THESE TERMS OR THE SERVICE, REGARDLESS OF THE LEGAL THEORY AND EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.",
      "SPRNTLY'S TOTAL AGGREGATE LIABILITY ARISING OUT OF OR IN CONNECTION WITH THESE TERMS WILL NOT EXCEED THE GREATER OF (A) THE AMOUNTS YOU PAID TO SPRNTLY IN THE TWELVE (12) MONTHS BEFORE THE EVENT GIVING RISE TO THE CLAIM AND (B) ONE HUNDRED U.S. DOLLARS ($100).",
      "These limitations apply notwithstanding the failure of essential purpose of any limited remedy and are an essential basis of the bargain between the parties.",
    ],
  },
  {
    id: "indemnification",
    title: "16. Indemnification",
    blocks: [
      "By you. You will defend, indemnify, and hold harmless Sprntly from third-party claims arising out of (a) your breach of these Terms; (b) your violation of law or third-party rights; (c) Customer Data; or (d) your use of Outputs.",
      "By us. We will defend you against claims that the Service, as provided by us and used in accordance with these Terms, infringes a U.S. patent, copyright, or trademark, and pay damages finally awarded, subject to standard exclusions for Customer Data, Outputs, unauthorized combinations, and your breach.",
      "Procedure. Indemnification is conditioned on prompt notice, sole control of defense and settlement, and reasonable cooperation at the indemnifying party's expense.",
    ],
  },
  {
    id: "governing-law",
    title: "17. Governing law and dispute resolution",
    blocks: [
      "These Terms are governed by the laws of the State of Delaware, without regard to conflict-of-laws principles. The United Nations Convention on Contracts for the International Sale of Goods does not apply.",
      "Any dispute arising out of or relating to these Terms will be brought exclusively in the state or federal courts located in Delaware, and the parties consent to personal jurisdiction. Each party waives any right to a jury trial.",
      "The parties will attempt in good faith to resolve disputes through informal discussions for thirty (30) days before initiating litigation, except that either party may seek injunctive relief for breach of confidentiality or IP rights.",
    ],
  },
  {
    id: "general",
    title: "18. General",
    blocks: [
      "Entire agreement. These Terms, together with any Customer Agreement, Order Form, our Privacy Policy, and our DPA, constitute the entire agreement and supersede prior agreements on the subject matter.",
      "Order of precedence. In the event of conflict: (1) Customer Agreement; (2) Order Form; (3) DPA (for personal information); (4) these Terms; (5) Privacy Policy.",
      "Changes to these Terms. We may update these Terms from time to time. Material changes take effect no earlier than thirty (30) days after notice by email or in the Service. Continued use after changes constitutes acceptance.",
      "Assignment. You may not assign these Terms without our prior written consent. We may assign in connection with a merger, acquisition, or sale of assets, on notice to you.",
      "Independent contractors. The parties are independent contractors. Nothing creates a partnership, joint venture, employment, agency, or fiduciary relationship.",
      "Notices. Notices to Sprntly must be sent to legal@sprntly.ai. We may give notice by email or posting in the Service.",
      "Force majeure. Neither party is liable for failure or delay caused by circumstances beyond reasonable control.",
      "Severability, waiver, government use, export controls, and headings. Standard provisions apply; government entities may contact legal@sprntly.ai for additional terms.",
    ],
  },
  {
    id: "contact",
    title: "Contact",
    blocks: [
      "Questions about these Terms can be sent to:",
      "Sprntly, Inc.",
      "Attn: Legal",
      "Email: legal@sprntly.ai",
      "General inquiries: build@sprntly.ai",
      "Mailing address: [To be updated — contact legal@sprntly.ai for our current business address.]",
    ],
  },
]
