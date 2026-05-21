/** Privacy policy copy — update dates here when the policy changes. */
export const PRIVACY_EFFECTIVE = "May 20, 2026"
export const PRIVACY_LAST_UPDATED = "May 20, 2026"

export type PolicySection = {
  id: string
  title: string
  blocks: string[]
}

export const PRIVACY_SECTIONS: PolicySection[] = [
  {
    id: "intro",
    title: "",
    blocks: [
      'Sprntly, Inc. ("Sprntly," "we," "us," or "our") is committed to maintaining strong privacy protections for the people and organizations who use our platform. This Privacy Policy explains how we collect, use, store, share, and safeguard information when you visit our website or use our services, and what choices you have about that information.',
      "This Privacy Policy applies to:",
      "sprntly.ai and any other Sprntly-operated websites that link to it (the \"Site\"); and",
      "the Sprntly Execution Intelligence platform and related services accessed through the Site, including our integrations, agents, and APIs (collectively, the \"Service\").",
      "By accessing the Site or using the Service, you agree to the practices described in this Privacy Policy and to our Terms of Service. If you do not agree, please do not use the Site or the Service.",
      'If you are using the Service as an employee or representative of a Sprntly customer ("Customer"), please read the section How We Handle Customer Data below carefully — your employer is responsible for the data they connect to Sprntly, and additional terms in their agreement with us may apply.',
    ],
  },
  {
    id: "who",
    title: "1. Who this policy applies to",
    blocks: [
      "We distinguish between two categories of information, because Sprntly handles them very differently:",
      "Personal Information is information about you as an individual user of the Site or Service — for example, your name, work email, the company you work for, and how you interact with the Service. We act as the controller of Personal Information.",
      'Customer Data is the business data a Customer organization connects to or generates within the Service — for example, anonymized product analytics events, support tickets, sales call transcripts, user research notes, code repositories, design files, documents from connected workspaces (such as Google Drive), and the priorities, briefs, and PRDs Sprntly generates from them. We process Customer Data on behalf of the Customer, as a processor (or "service provider," depending on jurisdiction). The Customer remains the controller of their Customer Data. Our handling of Customer Data is governed primarily by our agreement with that Customer, including our Data Processing Addendum (available on request).',
      "This Privacy Policy focuses on Personal Information. The section below on Customer Data explains the limited but important commitments we make about it.",
    ],
  },
  {
    id: "collect",
    title: "2. Information we collect",
    blocks: [
      "2.1 Information you provide directly",
      "When you sign up for, communicate with, or use the Service, you may provide us with:",
      "Your name, work email address, company name, role, and any other profile details you choose to share.",
      "Account credentials (such as a password or single sign-on identifier).",
      "Information you submit when you contact us — for example, a support request, a sales inquiry, or feedback.",
      "Information you submit when responding to a survey, signing up for an event, or applying for a job.",
      "Payment and billing information (collected and processed by our payment processors; we do not store full payment card numbers on our systems).",
      "2.2 Information collected automatically",
      "When you visit the Site or use the Service, we and our service providers may automatically collect certain information, including:",
      "Device and connection information — IP address, device type, operating system, browser type and version, time zone, and language settings.",
      "Usage information — pages or screens you view, features you interact with, links you click, the date and time of your visit, the referring URL, and similar interaction data.",
      "Cookies and similar technologies — small files stored on your device that help us recognize you across sessions, remember your preferences, secure your account, and measure how the Service is used. See the Cookies and Tracking Technologies section below for details.",
      "2.3 Information from third parties",
      "We may receive information about you from third parties, including:",
      "Identity providers (such as Google or Microsoft) if you choose to sign up or sign in using single sign-on.",
      "Business partners and integrations you connect to the Service, where they provide us with information about you in your capacity as a user of the Service.",
      "Publicly available sources and business information providers, in the course of sales, marketing, security, and fraud prevention activities.",
    ],
  },
  {
    id: "customer-data",
    title: "3. How we handle Customer Data",
    blocks: [
      "When a Customer organization connects tools to Sprntly — such as Amplitude, Mixpanel, Sentry, Salesforce, HubSpot, Zendesk, Intercom, Gong, Linear, Figma, GitHub, Google Drive, Google Analytics, Gmail, or similar systems — Sprntly ingests and processes the data those tools contain so the Service can generate insights, briefs, PRDs, prototypes, and recommendations for the Customer.",
      "We commit to the following:",
      "We process Customer Data only as instructed by the Customer, in accordance with our agreement with them. We do not use Customer Data to advertise, sell to third parties, or for any purpose outside the Service that is not authorized by the Customer.",
      "We may use Customer Data to operate, secure, monitor, and improve the Service for that Customer, including to generate the Customer's own insights, briefs, recommendations, and outputs.",
      "We may use de-identified and aggregated information derived from Customer Data — meaning information that does not identify any individual or specific Customer — to improve the Service, train and evaluate models we use to power the Service, develop new features, and produce benchmarks and research. Where applicable, we contractually agree with Customers on the scope of this use, and Customers may opt out of model improvement uses under the terms of their agreement.",
      "We do not sell Customer Data, and we do not share it with third parties except (a) with the Customer's sub-processors we engage to provide the Service, under written contracts requiring at least the same level of protection; (b) where the Customer directs us to do so; or (c) where required by law.",
      "Customers control retention and deletion of their Customer Data through the Service and through their agreement with us. When a Customer's agreement ends, we will delete or return Customer Data in accordance with that agreement and applicable law.",
      "If you are an end user whose data appears in Customer Data — for example, your support ticket was ingested by your employer — your relationship with respect to that data is with the Customer (typically your employer or the company whose product you use), not with Sprntly. You should direct privacy requests about that data to the Customer in the first instance. We will support Customers in responding to those requests as required by law.",
    ],
  },
  {
    id: "use",
    title: "4. How we use information",
    blocks: [
      "We use Personal Information to:",
      "Provide, operate, maintain, and improve the Site and the Service.",
      "Create and manage your account, authenticate you, and provide customer support.",
      "Communicate with you about your account, security alerts, product changes, and other administrative messages.",
      "Send you marketing communications about features, content, events, and offerings we think you may find relevant, where permitted by law. You can opt out of marketing emails at any time.",
      "Measure performance, debug issues, prevent fraud and abuse, and secure the Site and Service.",
      "Comply with our legal obligations, enforce our terms, and protect the rights, safety, and property of Sprntly, our users, and the public.",
      "We use automatically collected information to understand how the Site and Service are used, to improve and personalize the experience, and to maintain analytics and operational telemetry on an aggregate basis.",
    ],
  },
  {
    id: "cookies",
    title: "5. Cookies and tracking technologies",
    blocks: [
      "We and our service providers use cookies, pixels, local storage, and similar technologies to operate the Site and Service. Broadly, we use:",
      "Strictly necessary cookies — required for the Site and Service to function, including authentication and session management.",
      "Preference cookies — remember choices you have made.",
      "Analytics cookies — help us understand how the Site and Service are used so we can improve them.",
      "Marketing cookies — used in limited cases to measure the performance of our marketing.",
      "Most browsers let you control cookies through their settings. If you block strictly necessary cookies, parts of the Site or Service may not function correctly.",
      "We do not currently respond to browser \"Do Not Track\" signals in a uniform way, because there is no industry consensus on how to interpret them. Where required by law (for example, under California's CCPA/CPRA or similar U.S. state laws), we honor recognized opt-out preference signals.",
    ],
  },
  {
    id: "share",
    title: "6. How we share information",
    blocks: [
      "We do not sell Personal Information. We share Personal Information only as described below:",
      "Service providers and sub-processors. We share Personal Information with vendors that help us operate the Site and Service — for example, cloud hosting, security monitoring, customer support, analytics, email delivery, and payment processing. These vendors are bound by written contracts that require them to protect the information and use it only to provide services to us.",
      "Model and AI providers. The Service uses third-party large language model and AI infrastructure providers to power certain features. Where these providers process Personal Information or Customer Data on our behalf, they do so as sub-processors under contracts that prohibit them from using the data to train their own foundation models without our and the Customer's authorization.",
      "Affiliates. We may share information with our corporate affiliates for the purposes described in this Privacy Policy.",
      "Business transfers. If we are involved in a merger, acquisition, financing, reorganization, bankruptcy, or sale of assets, Personal Information may be transferred as part of that transaction, subject to standard confidentiality protections. We will notify you and, where required, obtain your consent.",
      "Legal and safety reasons. We may disclose information if we believe in good faith that disclosure is necessary to comply with a legal obligation, enforce our terms, investigate fraud or security incidents, or protect the rights, safety, and property of Sprntly, our users, or the public.",
      "With your direction. We may share Personal Information with third parties when you direct us to do so — for example, when you connect a third-party integration.",
    ],
  },
  {
    id: "protect",
    title: "7. How we protect information",
    blocks: [
      "We implement administrative, technical, and organizational measures designed to protect Personal Information and Customer Data from unauthorized access, alteration, disclosure, or destruction. These include encryption in transit and at rest, network and application security controls, access controls and least-privilege practices, logging and monitoring, vendor risk management, and employee training.",
      "No system is perfectly secure, however. We cannot guarantee that information will never be accessed, disclosed, altered, or destroyed by breach of our safeguards. You play an important role: keep your account password confidential, log out after using the Service on shared devices, and report suspected security issues to us at security@sprntly.ai.",
    ],
  },
  {
    id: "retention",
    title: "8. How long we keep information",
    blocks: [
      "We retain Personal Information for as long as needed to provide the Service, comply with our legal obligations, resolve disputes, and enforce our agreements. Retention periods depend on the type of information, the purpose for which we collected it, and applicable legal requirements. When information is no longer needed, we delete or de-identify it.",
      "We retain Customer Data in accordance with each Customer's instructions and the terms of their agreement with us.",
    ],
  },
  {
    id: "international",
    title: "9. International transfers",
    blocks: [
      "Sprntly is based in the United States, and the information we collect may be processed in and transferred to the United States and other countries where we and our service providers operate. These countries may have data protection laws that are different from the laws in your country. Where required, we rely on appropriate transfer mechanisms (such as Standard Contractual Clauses) to safeguard international transfers.",
    ],
  },
  {
    id: "rights",
    title: "10. Your rights and choices",
    blocks: [
      "Depending on where you live, you may have rights with respect to your Personal Information, including the right to:",
      "Access the Personal Information we hold about you;",
      "Correct inaccurate or incomplete Personal Information;",
      "Delete your Personal Information, subject to certain exceptions;",
      "Restrict or object to certain processing of your Personal Information;",
      "Receive a copy of your Personal Information in a portable format;",
      "Withdraw consent where we are processing on the basis of consent; and",
      "Lodge a complaint with a supervisory authority.",
      "To exercise any of these rights, please contact us at privacy@sprntly.ai. We will respond within the time required by applicable law. We may need to verify your identity before acting on your request.",
      "If your Personal Information is part of Customer Data being processed by Sprntly on behalf of a Customer, please direct your request to that Customer in the first instance. We will support the Customer in responding as required by law.",
      "You can also:",
      "Unsubscribe from marketing emails by following the unsubscribe link in any such email, or by changing your preferences in your account settings. We will still send you administrative messages (such as security or account notifications) that are necessary for the Service.",
      "Manage cookies through your browser settings.",
    ],
  },
  {
    id: "children",
    title: "11. Children's privacy",
    blocks: [
      "Sprntly is a business tool. The Site and Service are not directed to, and we do not knowingly collect Personal Information from, children under the age of 16. If you believe a child has provided us with Personal Information, please contact us at privacy@sprntly.ai and we will take appropriate steps to delete it.",
    ],
  },
  {
    id: "third-party",
    title: "12. Third-party sites and integrations",
    blocks: [
      "The Site and Service may contain links to third-party websites, applications, and integrations. This Privacy Policy does not apply to those third parties. We encourage you to read the privacy policies of any third parties before using them. We are not responsible for the practices of third parties.",
    ],
  },
  {
    id: "changes",
    title: "13. Changes to this Privacy Policy",
    blocks: [
      "We may update this Privacy Policy from time to time to reflect changes to our practices, technologies, legal requirements, or other reasons. When we make changes, we will update the \"Last updated\" date above and, where the changes are significant, we will provide a more prominent notice — for example, by email to the address associated with your account or by a banner on the Site. Material changes will become effective no earlier than 30 days after the notice is provided, except where a shorter period is required by law.",
    ],
  },
  {
    id: "contact",
    title: "14. Contact us",
    blocks: [
      "If you have questions, requests, or concerns about this Privacy Policy or our privacy practices, please contact us at:",
      "Sprntly, Inc.",
      "Attn: Privacy",
      "Email: privacy@sprntly.ai",
      "General inquiries: build@sprntly.ai",
      "Security issues: security@sprntly.ai",
      "Mailing address: [To be updated — contact privacy@sprntly.ai for our current business address.]",
    ],
  },
]
