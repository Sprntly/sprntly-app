## Sheet: Executive_Summary  (rows=4, cols=8)
| rank | insight_title | domain | confidence | primary_metric | impact_dollar | effort | supporting_sheets |
| 1 | App-channel filers churn at 22% vs phone-channel at 64%. 42% of phone filers already have the app installed and use it monthly. | Retention | Channel | 0.91 | -42pp churn gap | $15M LTV/yr | 2 weeks | Channel_Retention; Phone_Filer_App_Status; Channel_Churn_By_Carrier; Channel_Churn_By_Device; Channel_Churn_By_Tenure; Channel_Churn_By_Age |
| 2 | iPhone 15 Pro users abandon 24-27% at photo upload due to 48MP camera files exceeding 30s timeout. | Activation | Mobile | 0.96 | 25% drop-off vs <1% all other devices | $2.2M margin/yr | 1 sprint | Upload_Failure_By_Device_And_OS; Upload_Failure_By_OS_Version; Upload_Failure_By_Network; Claim_Funnel_By_Step |
| 3 | Screen repair filers abandon at deductible disclosure (67% on app), get repair done by third-party, then cancel coverage. 47% churn within 30 days of abandonment. | Churn | Pricing | 0.94 | 67% drop-off, 47% churn, third-party repair sequence | $143M ARR at risk | Pricing review | Deductible_Drop_Off_By_Channel; Post_Abandonment_Outcome; Deductible_Abandonment_By_Tier; Deductible_Abandonment_By_Age; Deductible_Abandon_By_Tenure; Churn_By_Drop_Step |

## Sheet: Channel_Retention  (rows=5, cols=8)
| filing_channel | claims_count | avg_per_claim_cost_usd | churn_rate_30d_pct | churn_rate_90d_pct | churn_rate_180d_pct | ltv_after_claim_usd | renewal_rate_pct |
| app | 14900000 | 3.2 | 12 | 22 | 28.5 | 168.4 | 78 |
| phone | 30100000 | 22.1 | 38 | 64 | 71.5 | 52.8 | 36 |
| retail | 3010000 | 8.4 | 12.5 | 22.5 | 28 | 162.2 | 77.5 |
| web | 1990000 | 12.6 | 13 | 23 | 29 | 165 | 77 |

## Sheet: Phone_Filer_App_Status  (rows=5, cols=6)
| phone_filer_segment | share_of_phone_filers_pct | share_with_app_session_30d_pct | share_with_app_session_7d_pct | avg_app_sessions_per_month | implication |
| Has app installed AND active in last 7 days | 32.8 | 100 | 100 | 14.2 | Highly active app user — chose phone anyway. Most addressable cohort for migration. |
| Has app installed AND active in last 30 days (not 7d) | 9.2 | 100 | 0 | 4.8 | Active but less recent. Still addressable. |
| Has app installed but no recent session | 4.8 | 0 | 0 | 0.3 | Likely forgot the app exists. Reactivation needed. |
| No app installed | 53.2 | 0 | 0 | 0 | Acquisition target (app install). |

## Sheet: Channel_Churn_By_Carrier  (rows=9, cols=4)
| carrier | channel | claims_share_pct | churn_rate_90d_pct |
| Verizon | app | 38 | 22.5 |
| Verizon | phone | 38 | 64.5 |
| AT&T | app | 31 | 21.8 |
| AT&T | phone | 31 | 63.8 |
| T-Mobile | app | 24 | 22.2 |
| T-Mobile | phone | 24 | 64.2 |
| Direct | app | 7 | 21 |
| Direct | phone | 7 | 63.5 |

## Sheet: Channel_Churn_By_Device  (rows=17, cols=3)
| device_model | channel | churn_rate_90d_pct |
| iPhone 15 Pro | app | 22.5 |
| iPhone 15 Pro | phone | 64 |
| iPhone 15 | app | 21.8 |
| iPhone 15 | phone | 63.5 |
| iPhone 14 | app | 22.2 |
| iPhone 14 | phone | 64.2 |
| iPhone 13 | app | 21.5 |
| iPhone 13 | phone | 64.8 |
| iPhone 12 | app | 22 |
| iPhone 12 | phone | 63 |
| Samsung Galaxy S24 | app | 22.8 |
| Samsung Galaxy S24 | phone | 64.5 |
| Samsung Galaxy S23 | app | 21.5 |
| Samsung Galaxy S23 | phone | 63.8 |
| Google Pixel 8 | app | 22 |
| Google Pixel 8 | phone | 64 |

## Sheet: Channel_Churn_By_Tenure  (rows=9, cols=3)
| tenure_bucket | channel | churn_rate_90d_pct |
| 0-6 months | app | 22 |
| 0-6 months | phone | 64.5 |
| 6-12 months | app | 21.5 |
| 6-12 months | phone | 63.8 |
| 1-2 years | app | 22.5 |
| 1-2 years | phone | 64 |
| 2+ years | app | 22.2 |
| 2+ years | phone | 63.5 |

## Sheet: Channel_Churn_By_Age  (rows=9, cols=3)
| age_segment | channel | churn_rate_90d_pct |
| 18-29 | app | 22 |
| 18-29 | phone | 63.8 |
| 30-44 | app | 22.5 |
| 30-44 | phone | 64.2 |
| 45-59 | app | 21.8 |
| 45-59 | phone | 64 |
| 60+ | app | 22.2 |
| 60+ | phone | 64.5 |

## Sheet: Upload_Failure_By_Device_And_OS  (rows=22, cols=8)
| device_model | os_version | upload_attempts | avg_file_size_mb | avg_upload_duration_sec | success_rate_pct | timeout_failure_rate_pct | primary_error_code |
| iPhone 15 Pro Max | iOS 18.2 | 5240 | 19.8 | 38.5 | 71 | 27.4 | ERR_UPLOAD_TIMEOUT_30S |
| iPhone 15 Pro Max | iOS 18.1 | 3120 | 19.6 | 38.1 | 72.5 | 26 | ERR_UPLOAD_TIMEOUT_30S |
| iPhone 15 Pro | iOS 18.2 | 6920 | 18.4 | 35.8 | 73.5 | 24.6 | ERR_UPLOAD_TIMEOUT_30S |
| iPhone 15 Pro | iOS 18.1 | 4180 | 18.1 | 34.9 | 75.2 | 22.9 | ERR_UPLOAD_TIMEOUT_30S |
| iPhone 15 Pro | iOS 17.6 | 2640 | 17.9 | 34.5 | 75.8 | 22.4 | ERR_UPLOAD_TIMEOUT_30S |
| iPhone 15 | iOS 18.2 | 5410 | 8.9 | 14.5 | 99.1 | 0.4 | (none) |
| iPhone 15 | iOS 18.1 | 3960 | 8.7 | 14.2 | 99.2 | 0.3 | (none) |
| iPhone 14 Pro | iOS 18.2 | 4800 | 11.2 | 17.8 | 98.4 | 0.7 | (none) |
| iPhone 14 Pro | iOS 17.6 | 3200 | 11 | 17.5 | 98.6 | 0.6 | (none) |
| iPhone 14 | iOS 18.2 | 8200 | 7.2 | 11.8 | 99.2 | 0.2 | (none) |
| iPhone 14 | iOS 17.6 | 4100 | 7.1 | 11.6 | 99.3 | 0.2 | (none) |
| iPhone 13 | iOS 18.2 | 3800 | 6 | 10.1 | 99.4 | 0.1 | (none) |
| iPhone 13 | iOS 17.5 | 4850 | 5.9 | 10 | 99.5 | 0.1 | (none) |
| iPhone 12 | iOS 17.4 | 5320 | 5 | 8.6 | 99.5 | 0.1 | (none) |
| iPhone 11 | iOS 16.7 | 3970 | 4.4 | 7.6 | 99.6 | 0.1 | (none) |
| Samsung Galaxy S24 Ultra | Android 14 | 4100 | 8.5 | 13.8 | 99 | 0.4 | (none) |
| Samsung Galaxy S24 | Android 14 | 4520 | 7.4 | 12.2 | 99.1 | 0.3 | (none) |
| Samsung Galaxy S23 | Android 14 | 5240 | 6.8 | 11.1 | 99.2 | 0.3 | (none) |
| Samsung Galaxy S22 | Android 13 | 2840 | 6.2 | 10.5 | 99.3 | 0.3 | (none) |
| Google Pixel 8 Pro | Android 14 | 1620 | 7.5 | 12.4 | 99 | 0.4 | (none) |
| Google Pixel 8 | Android 14 | 2410 | 6.3 | 10.6 | 99.2 | 0.3 | (none) |

## Sheet: Upload_Failure_By_OS_Version  (rows=12, cols=4)
| os_version | upload_attempts | success_rate_pct | interpretation |
| iOS 18.2 (all devices) | 35400 | 95.6 | Includes iPhone 15 Pro at 27% failure; weighted avg masks device variance |
| iOS 18.2 (excluding iPhone 15 Pro family) | 22210 | 99.2 | Flat — same as older iOS |
| iOS 18.1 (all devices) | 11260 | 95.2 | Same pattern — 15 Pro family elevates |
| iOS 18.1 (excluding iPhone 15 Pro family) | 3960 | 99.2 | Flat |
| iOS 17.6 (all devices) | 9940 | 95.4 | Same pattern |
| iOS 17.6 (excluding iPhone 15 Pro) | 7300 | 99 | Flat |
| iOS 17.5 | 4850 | 99.5 | Flat — no 15 Pro on this OS |
| iOS 17.4 | 5320 | 99.5 | Flat |
| iOS 16.7 | 3970 | 99.6 | Flat |
| Android 14 (all devices) | 12650 | 99.1 | Flat |
| Android 13 | 2840 | 99.3 | Flat |

## Sheet: Upload_Failure_By_Network  (rows=9, cols=5)
| network_type | device_class | upload_attempts | avg_upload_duration_sec | success_rate_pct |
| WiFi | iPhone 15 Pro family | 6400 | 22.4 | 88 |
| WiFi | All other devices | 19500 | 8.2 | 99.7 |
| 5G | iPhone 15 Pro family | 3400 | 28.6 | 76 |
| 5G | All other devices | 9800 | 11.5 | 99.4 |
| LTE | iPhone 15 Pro family | 3100 | 39.2 | 64 |
| LTE | All other devices | 8900 | 14.1 | 99 |
| 4G | iPhone 15 Pro family | 2000 | 56.8 | 38 |
| 4G | All other devices | 5700 | 18.4 | 98.5 |

## Sheet: Deductible_Drop_Off_By_Channel  (rows=11, cols=6)
| claim_type | channel | users_reaching_deductible_step | users_abandoning_at_deductible | abandonment_rate_pct | interpretation |
| screen_repair | app | 9020 | 6040 | 67 | Highest — self-service flow, customer compares price freely with no friction to leave |
| screen_repair | web | 1450 | 840 | 58 | High — desktop self-service, customer can google iFixit pricing in same session |
| screen_repair | phone | 22500 | 7875 | 35 | Lower — agent can offer hardship discount or apply retention save script; still significant |
| screen_repair | retail | 1800 | 504 | 28 | Lowest — customer is physically present, has already invested travel time, and uBreakiFix tech can complete same day |
| replacement_damage | app | 7280 | 430 | 5.9 | Baseline — replacement cost is far higher than third-party alternatives |
| replacement_damage | web | 1220 | 78 | 6.4 | Baseline |
| replacement_damage | phone | 18400 | 1100 | 6 | Baseline |
| replacement_damage | retail | 1640 | 100 | 6.1 | Baseline |
| replacement_loss | app | 5180 | 280 | 5.4 | Baseline |
| replacement_loss | phone | 13200 | 730 | 5.5 | Baseline |

## Sheet: Deductible_Abandonment_By_Tier  (rows=7, cols=6)
| deductible_tier | deductible_amount_usd | third_party_market_price_usd | asurion_price_premium_pct | abandonment_rate_pct | subsequent_30d_churn_pct |
| Tier 1 (older devices) | 49 | 65 | -25 | 22 | 28.5 |
| Tier 1 (older devices) | 79 | 75 | 5 | 38 | 35.5 |
| Tier 2 (mid-range) | 99 | 70 | 41 | 58 | 41 |
| Tier 2 (mid-range) | 109 | 75 | 45 | 62 | 43 |
| Tier 3 (premium) | 129 | 80 | 61 | 67.5 | 47 |
| Tier 3 (premium) | 149 | 85 | 75 | 72 | 49.5 |

## Sheet: Deductible_Abandonment_By_Age  (rows=5, cols=4)
| age_segment | screen_repair_app_filings | abandonment_rate_pct | subsequent_30d_churn_pct |
| 18-29 | 1980 | 67.5 | 47.2 |
| 30-44 | 3060 | 66.8 | 47 |
| 45-59 | 2520 | 67.2 | 46.8 |
| 60+ | 1440 | 66.5 | 47.5 |

## Sheet: Deductible_Abandon_By_Tenure  (rows=5, cols=4)
| tenure_bucket | screen_repair_app_filings | abandonment_rate_pct | subsequent_30d_churn_pct |
| 0-6 months | 900 | 66.8 | 47 |
| 6-12 months | 1800 | 67.2 | 47.2 |
| 1-2 years | 3150 | 67 | 47 |
| 2+ years | 3150 | 67.4 | 46.9 |

## Sheet: Post_Abandonment_Outcome  (rows=6, cols=4)
| sequence_step | share_of_screen_abandonment_cohort_pct | evidence_source | explanation |
| 1. Abandoned at deductible disclosure | 100 | Claim_Funnel_By_Step (status='abandoned' AND step='deductible_disclosed') | User saw deductible amount, exited claim flow |
| 2. Searched for third-party repair within 7 days | 71 | Mobile app referrer logs + competitive web traffic data (third-party panel) | Same user device pings iFixit, ubreakifix.com (organic), or local repair shop search results |
| 3. Confirmed third-party repair completed within 14 days | 54 | Reverse lookup: device IMEI seen at Asurion-owned uBreakiFix walk-in (no insurance) OR new Genius bar appointment OR third-party repair partner data exchange | Direct evidence the repair happened outside the Asurion claim flow |
| 4. Cancelled Asurion coverage within 30 days | 47 | Salesforce subscription_status = cancelled within 30d of abandonment | Cancellation reason on exit survey: 'I can fix it cheaper elsewhere' (38% of cancel reasons in this cohort) |
| 5. Cancelled Asurion coverage within 90 days | 62.5 | Salesforce subscription_status | Cumulative — most cancellations occur in the 30-60 day window after abandonment |

## Sheet: Claim_Funnel_By_Step  (rows=27, cols=8)
| claim_type | channel | step_number | step_name | users_entered | users_completed | step_drop_off_pct | is_anomalous |
| screen_repair | app | 1 | claim_initiated | 12450 | 12100 | 2.8 | no |
| screen_repair | app | 2 | device_selected | 12100 | 11920 | 1.5 | no |
| screen_repair | app | 3 | damage_described | 11920 | 11650 | 2.3 | no |
| screen_repair | app | 4 | photo_uploaded | 11650 | 9020 | 22.6 | YES — iPhone 15 Pro driven (Insight #2) |
| screen_repair | app | 5 | deductible_disclosed | 9020 | 2980 | 67 | YES — Insight #3 deductible drop |
| screen_repair | app | 6 | payment_completed | 2980 | 2890 | 3 | no |
| screen_repair | app | 7 | claim_submitted | 2890 | 2872 | 0.6 | no |
| replacement_damage | app | 1 | claim_initiated | 8200 | 8050 | 1.8 | no |
| replacement_damage | app | 2 | device_selected | 8050 | 7950 | 1.2 | no |
| replacement_damage | app | 3 | damage_described | 7950 | 7780 | 2.1 | no |
| replacement_damage | app | 4 | photo_uploaded | 7780 | 7280 | 6.4 | elevated — iPhone 15 Pro driven (Insight #2) |
| replacement_damage | app | 5 | deductible_disclosed | 7280 | 6850 | 5.9 | no — baseline |
| replacement_damage | app | 6 | payment_completed | 6850 | 6760 | 1.3 | no |
| replacement_damage | app | 7 | claim_submitted | 6760 | 6750 | 0.1 | no |
| replacement_loss | app | 1 | claim_initiated | 5400 | 5320 | 1.5 | no |
| replacement_loss | app | 2 | device_selected | 5320 | 5240 | 1.5 | no |
| replacement_loss | app | 3 | loss_circumstances_described | 5240 | 5180 | 1.1 | no |
| replacement_loss | app | 4 | deductible_disclosed | 5180 | 4900 | 5.4 | no — baseline |
| replacement_loss | app | 5 | payment_completed | 4900 | 4860 | 0.8 | no |
| replacement_loss | app | 6 | claim_submitted | 4860 | 4855 | 0.1 | no |
| battery_replacement | app | 1 | claim_initiated | 2100 | 2070 | 1.4 | no |
| battery_replacement | app | 2 | device_selected | 2070 | 2040 | 1.4 | no |
| battery_replacement | app | 3 | battery_health_check | 2040 | 2010 | 1.5 | no |
| battery_replacement | app | 4 | deductible_disclosed | 2010 | 1900 | 5.5 | no — baseline |
| battery_replacement | app | 5 | payment_completed | 1900 | 1880 | 1.1 | no |
| battery_replacement | app | 6 | claim_submitted | 1880 | 1875 | 0.3 | no |

## Sheet: Churn_By_Drop_Step  (rows=8, cols=7)
| claim_type | drop_off_step | cohort_size | churned_within_30d_pct | churned_within_90d_pct | ltv_lost_per_user_usd | share_of_total_monthly_churn_pct |
| screen_repair | deductible_disclosed | 6040 | 47 | 62.5 | 71.2 | 22 |
| screen_repair | photo_uploaded | 2630 | 30 | 41 | 102.5 | 5.5 |
| screen_repair | completed_claim | 2872 | 18 | 24.5 | 142.8 | 4 |
| replacement_damage | deductible_disclosed | 430 | 22 | 28 | 118.4 | 0.6 |
| replacement_damage | completed_claim | 6750 | 20 | 24 | 152.2 | 6.5 |
| replacement_loss | completed_claim | 4855 | 20 | 24.5 | 148.4 | 5.2 |
| battery_replacement | completed_claim | 1875 | 18.5 | 23 | 138.5 | 1 |

## Sheet: Support_Calls_By_Reason  (rows=10, cols=5)
| call_reason | monthly_volume | avg_handle_time_min | avg_cost_per_call_usd | supports_top_insight |
| file_new_claim | 32400 | 9.5 | 22.1 | Insight #1 — phone filers who could be on app |
| photo_upload_help | 9600 | 11.2 | 25.4 | Insight #2 — iPhone 15 Pro upload failures |
| deductible_question | 6400 | 8.4 | 19.1 | Insight #3 — screen repair deductible objections |
| billing_question | 4800 | 7.2 | 17.4 | (baseline) |
| check_claim_status | 3900 | 7.8 | 18.2 | (baseline — distributed across channels) |
| general_question | 3900 | 6.8 | 16.5 | (baseline) |
| device_compatibility | 2700 | 9.1 | 20.4 | (baseline) |
| cancel_coverage | 2400 | 11.5 | 25.8 | (baseline) |
| complaint_unresolved | 2100 | 14.2 | 31.2 | (baseline) |

## Sheet: Cross_Check_No_Spurious_Signals  (rows=10, cols=4)
| dimension | variance_range | interpretation | could_compete_with_top_3 |
| Carrier (Verizon, AT&T, T-Mobile, Direct) | <2 pp on app and on phone separately | Flat — channel gap holds across all carriers | No |
| Age segment (18-29 / 30-44 / 45-59 / 60+) | <1 pp on each variable | Flat | No |
| Tenure bucket (0-6mo / 6-12mo / 1-2yr / 2yr+) | <1 pp on each variable | Flat | No |
| iOS version (excluding iPhone 15 Pro family) | all 99.0-99.6% upload success | OS alone explains nothing — failure follows device | No |
| Network type (excluding iPhone 15 Pro) | 98.5-99.7% upload success | Network alone explains nothing — failure follows device + file size | No |
| Device model (cross-channel app vs phone) | 21.5-22.8% (app) and 63.0-64.8% (phone) | Channel gap holds; no single device pops in churn | No |
| Demographic on deductible abandonment | <1 pp across age and tenure | Abandonment is price-driven, not demographic | No |
| Filing time of day | no pattern | Flat across all hours | No |
| Geography (region) | no pattern | Flat across all US regions | No |

## Sheet: Claims_Filings  (rows=3001, cols=20)
| claim_id | user_id | filing_date | filing_channel | device_model | device_os | claim_type | claim_status | deductible_charged | user_has_app_installed | user_app_session_within_30d | user_app_session_count_90d | claim_completed_in_app | abandoned_at_step | post_abandonment_third_party_repair | post_abandonment_cancelled_within_30d | churned_within_90d | tenure_months_at_claim | carrier | user_age_segment |
| CLM-100000 | USR-200000 | 2026-04-13 | app | Samsung Galaxy S24 | Android 14 | screen_repair | completed | 99 | True | True | 12 | True |  | False | False | False | 7 | AT&T | 45-59 |
| CLM-100001 | USR-200001 | 2026-04-16 | phone | iPhone 14 | iOS 18.2 | replacement_loss | completed | 229 | True | False | 0 | False |  | False | False | True | 14 | Verizon | 30-44 |
| CLM-100002 | USR-200002 | 2026-01-07 | phone | Samsung Galaxy S24 Ultra | Android 14 | replacement_loss | completed | 229 | False | False | 0 | False |  | False | False | False | 31 | Verizon | 30-44 |
| CLM-100003 | USR-200003 | 2026-01-14 | web | Samsung Galaxy S22 | Android 13 | replacement_theft | completed | 229 | True | True | 5 | False |  | False | False | True | 4 | AT&T | 30-44 |
| CLM-100004 | USR-200004 | 2026-01-02 | app | iPhone 14 | iOS 18.2 | battery_replacement | completed | 199 | True | True | 28 | True |  | False | False | False | 13 | Verizon | 60+ |
| CLM-100005 | USR-200005 | 2026-03-26 | phone | iPhone 14 Pro | iOS 18.2 | replacement_damage | completed | 199 | True | False | 1 | False |  | False | False | True | 17 | T-Mobile | 30-44 |
| CLM-100006 | USR-200006 | 2025-12-27 | web | iPhone 15 Pro | iOS 18.2 | replacement_loss | completed | 199 | False | False | 1 | False |  | False | False | False | 14 | Direct | 30-44 |
| CLM-100007 | USR-200007 | 2026-03-22 | phone | iPhone 15 Pro Max | iOS 18.2 | replacement_damage | completed | 229 | False | False | 0 | False |  | False | False | False | 7 | Verizon | 45-59 |
| CLM-100008 | USR-200008 | 2026-01-07 | web | Samsung Galaxy S24 | Android 14 | replacement_loss | completed | 199 | True | False | 10 | False |  | False | False | True | 21 | AT&T | 18-29 |
| CLM-100009 | USR-200009 | 2026-02-01 | phone | Samsung Galaxy S23 | Android 14 | screen_repair | completed | 99 | True | False | 0 | False |  | False | False | False | 15 | AT&T | 30-44 |
| CLM-100010 | USR-200010 | 2026-04-05 | phone | iPhone 15 | iOS 18.2 | replacement_loss | completed | 229 | False | False | 0 | False |  | False | False | True | 10 | Verizon | 30-44 |
| CLM-100011 | USR-200011 | 2026-03-31 | phone | iPhone 12 | iOS 17.4 | screen_repair | completed | 79 | False | False | 0 | False |  | False | False | False | 33 | Verizon | 18-29 |
| CLM-100012 | USR-200012 | 2026-03-27 | phone | iPhone 15 | iOS 18.2 | replacement_loss | completed | 249 | True | True | 18 | False |  | False | False | True | 2 | AT&T | 30-44 |
| CLM-100013 | USR-200013 | 2025-11-28 | app | iPhone 15 Pro | iOS 17.6 | replacement_loss | completed | 249 | True | True | 21 | True |  | False | False | False | 2 | Direct | 45-59 |
| CLM-100014 | USR-200014 | 2025-11-24 | retail | Samsung Galaxy S23 | Android 14 | screen_repair | abandoned | 0 | True | True | 6 | False | deductible_disclosure | True | True | True | 12 | Verizon | 60+ |
| CLM-100015 | USR-200015 | 2026-03-29 | phone | iPhone 15 Pro Max | iOS 18.1 | screen_repair | abandoned | 0 | True | True | 21 | False | deductible_disclosure | True | False | False | 10 | AT&T | 45-59 |
| CLM-100016 | USR-200016 | 2026-02-16 | phone | iPhone 12 | iOS 17.4 | screen_repair | completed | 79 | True | True | 15 | False |  | False | False | False | 4 | AT&T | 45-59 |
| CLM-100017 | USR-200017 | 2025-11-26 | app | iPhone 15 | iOS 18.2 | screen_repair | completed | 129 | True | True | 19 | True |  | False | False | False | 24 | Direct | 45-59 |
| CLM-100018 | USR-200018 | 2025-11-03 | phone | iPhone 13 | iOS 17.5 | battery_replacement | completed | 149 | True | False | 1 | False |  | False | False | False | 9 | Verizon | 45-59 |
| CLM-100019 | USR-200019 | 2026-01-07 | phone | iPhone 15 | iOS 18.1 | replacement_theft | abandoned | 0 | True | True | 13 | False | deductible_disclosure | False | False | False | 24 | AT&T | 18-29 |
| CLM-100020 | USR-200020 | 2025-11-20 | retail | Samsung Galaxy S24 Ultra | Android 14 | screen_repair | completed | 109 | True | True | 8 | False |  | False | False | False | 9 | Verizon | 45-59 |
| CLM-100021 | USR-200021 | 2026-02-13 | web | Samsung Galaxy S24 | Android 14 | battery_replacement | completed | 149 | True | True | 2 | False |  | False | False | True | 48 | Verizon | 45-59 |
| CLM-100022 | USR-200022 | 2025-11-28 | phone | iPhone 15 Pro Max | iOS 18.2 | replacement_loss | completed | 199 | True | False | 1 | False |  | False | False | True | 33 | Direct | 30-44 |
| CLM-100023 | USR-200023 | 2026-04-14 | phone | iPhone 11 | iOS 16.7 | replacement_theft | completed | 229 | True | True | 13 | False |  | False | False | True | 48 | AT&T | 60+ |
| CLM-100024 | USR-200024 | 2025-11-30 | phone | iPhone 12 | iOS 17.4 | screen_repair | completed | 79 | False | False | 0 | False |  | False | False | True | 18 | T-Mobile | 45-59 |
| CLM-100025 | USR-200025 | 2026-01-16 | phone | iPhone 11 | iOS 16.7 | screen_repair | completed | 79 | False | False | 0 | False |  | False | False | False | 22 | T-Mobile | 30-44 |
| CLM-100026 | USR-200026 | 2025-12-24 | phone | iPhone 12 | iOS 17.4 | replacement_damage | completed | 199 | False | False | 0 | False |  | False | False | True | 14 | AT&T | 30-44 |
| CLM-100027 | USR-200027 | 2025-12-31 | phone | iPhone 15 | iOS 18.2 | screen_repair | completed | 109 | True | True | 20 | False |  | False | False | False | 10 | AT&T | 60+ |
| CLM-100028 | USR-200028 | 2025-11-28 | phone | iPhone 15 | iOS 18.2 | replacement_damage | completed | 199 | False | False | 0 | False |  | False | False | False | 39 | Direct | 45-59 |
... [truncated, 2971 more rows]

## Sheet: Photo_Upload_Events  (rows=1801, cols=14)
| event_id | claim_id | user_id | device_model | os_version | network_type | photo_file_size_mb | upload_started_at | upload_duration_seconds | upload_status | upload_error_code | user_retried | user_abandoned_after_failure | resolution_path |
| EVT-500000 | CLM-100427 | USR-202531 | iPhone 15 Pro Max | iOS 18.2 | WiFi | 19.9 | 2025-11-06 09:00 | 18.9 | success |  | False | False | completed_in_app |
| EVT-500001 | CLM-101470 | USR-200254 | iPhone 13 | iOS 18.2 | LTE | 5.6 | 2026-01-10 20:00 | 12.9 | success |  | False | False | completed_in_app |
| EVT-500002 | CLM-101305 | USR-200288 | iPhone 15 Pro | iOS 18.1 | 5G | 18.8 | 2026-01-09 20:00 | 24.2 | success |  | False | False | completed_in_app |
| EVT-500003 | CLM-100356 | USR-202080 | iPhone 15 | iOS 18.2 | LTE | 7.3 | 2026-01-18 10:00 | 11 | success |  | False | False | completed_in_app |
| EVT-500004 | CLM-101433 | USR-200096 | iPhone 14 | iOS 17.6 | 4G | 5.9 | 2025-12-22 17:00 | 19.3 | success |  | False | False | completed_in_app |
| EVT-500005 | CLM-101456 | USR-202757 | iPhone 15 Pro Max | iOS 18.1 | LTE | 17.3 | 2026-01-25 08:00 | 33 | failed_timeout | ERR_UPLOAD_TIMEOUT_30S | False | True | no_resolution |
| EVT-500006 | CLM-100141 | USR-201314 | iPhone 15 Pro | iOS 17.6 | LTE | 18.2 | 2026-02-07 19:00 | 42 | failed_timeout | ERR_UPLOAD_TIMEOUT_30S | False | True | no_resolution |
| EVT-500007 | CLM-100244 | USR-202671 | iPhone 15 | iOS 18.2 | LTE | 6.8 | 2025-12-17 12:00 | 16.3 | success |  | False | False | completed_in_app |
| EVT-500008 | CLM-101932 | USR-201150 | iPhone 15 Pro | iOS 18.2 | WiFi | 20.1 | 2025-12-09 17:00 | 22.4 | success |  | False | False | completed_in_app |
| EVT-500009 | CLM-101294 | USR-201788 | iPhone 14 | iOS 18.2 | WiFi | 6.1 | 2026-04-26 10:00 | 7 | success |  | False | False | completed_in_app |
| EVT-500010 | CLM-102864 | USR-201083 | iPhone 11 | iOS 16.7 | WiFi | 3.8 | 2026-04-28 15:00 | 2.8 | success |  | False | False | completed_in_app |
| EVT-500011 | CLM-102790 | USR-201481 | Samsung Galaxy S24 | Android 14 | 5G | 4.6 | 2026-02-09 08:00 | 5.2 | success |  | False | False | completed_in_app |
| EVT-500012 | CLM-100327 | USR-202017 | iPhone 11 | iOS 16.7 | LTE | 3.9 | 2025-11-16 08:00 | 6.7 | success |  | False | False | completed_in_app |
| EVT-500013 | CLM-102983 | USR-202409 | iPhone 15 Pro | iOS 18.1 | WiFi | 18.3 | 2026-01-24 22:00 | 14.7 | success |  | False | False | completed_in_app |
| EVT-500014 | CLM-102459 | USR-202681 | Google Pixel 8 Pro | Android 14 | LTE | 6.7 | 2026-03-23 15:00 | 11.1 | success |  | False | False | completed_in_app |
| EVT-500015 | CLM-101956 | USR-201960 | iPhone 14 | iOS 18.2 | WiFi | 5.8 | 2025-12-02 12:00 | 4.9 | success |  | False | False | completed_in_app |
| EVT-500016 | CLM-101411 | USR-200468 | Samsung Galaxy S22 | Android 13 | 5G | 5.8 | 2026-04-15 20:00 | 5.2 | success |  | False | False | completed_in_app |
| EVT-500017 | CLM-100669 | USR-201712 | Samsung Galaxy S23 | Android 14 | LTE | 5.8 | 2026-03-26 11:00 | 10.6 | success |  | False | False | completed_in_app |
| EVT-500018 | CLM-102734 | USR-201541 | iPhone 13 | iOS 18.2 | WiFi | 4.9 | 2026-01-06 08:00 | 5.3 | success |  | False | False | completed_in_app |
| EVT-500019 | CLM-100959 | USR-201249 | iPhone 12 | iOS 17.4 | 4G | 4.6 | 2026-02-23 13:00 | 15.9 | success |  | False | False | completed_in_app |
| EVT-500020 | CLM-102540 | USR-201466 | iPhone 14 Pro | iOS 17.6 | WiFi | 7.3 | 2025-11-13 21:00 | 8.5 | success |  | False | False | completed_in_app |
| EVT-500021 | CLM-102369 | USR-201073 | iPhone 14 | iOS 17.6 | WiFi | 7.3 | 2026-03-10 22:00 | 6.9 | success |  | False | False | completed_in_app |
| EVT-500022 | CLM-102902 | USR-202860 | iPhone 15 | iOS 18.2 | 5G | 7.7 | 2026-03-24 19:00 | 10.9 | success |  | False | False | completed_in_app |
| EVT-500023 | CLM-102683 | USR-201918 | iPhone 14 | iOS 18.2 | LTE | 6.7 | 2026-01-01 16:00 | 15 | success |  | False | False | completed_in_app |
| EVT-500024 | CLM-102212 | USR-201356 | Google Pixel 8 Pro | Android 14 | WiFi | 7.3 | 2026-01-30 20:00 | 6.8 | success |  | False | False | completed_in_app |
| EVT-500025 | CLM-102824 | USR-202120 | iPhone 15 | iOS 18.2 | 5G | 7.5 | 2026-03-30 17:00 | 7.9 | success |  | False | False | completed_in_app |
| EVT-500026 | CLM-102822 | USR-202712 | iPhone 13 | iOS 18.2 | LTE | 5.1 | 2025-12-16 20:00 | 8.7 | success |  | False | False | completed_in_app |
| EVT-500027 | CLM-101148 | USR-200248 | iPhone 15 Pro | iOS 18.1 | 4G | 19.7 | 2026-04-28 14:00 | 63.6 | failed_timeout | ERR_UPLOAD_TIMEOUT_30S | False | True | filed_via_phone |
| EVT-500028 | CLM-102111 | USR-202151 | iPhone 14 | iOS 18.2 | LTE | 6.8 | 2026-04-09 08:00 | 14.4 | success |  | False | False | completed_in_app |
... [truncated, 1771 more rows]

## Sheet: App_Sessions  (rows=2401, cols=10)
| session_id | user_id | session_date | device_type | session_duration_min | screens_visited | visited_claims_section | viewed_file_claim_button | clicked_file_claim_button | session_ended_with_claim_filed |
| SES-700000 | USR-202391 | 2026-01-16 | mobile_app_ios | 10.2 | 3 | False | False | False | False |
| SES-700001 | USR-200287 | 2026-02-18 | mobile_app_android | 12.3 | 5 | False | False | False | False |
| SES-700002 | USR-202522 | 2026-03-14 | mobile_app_android | 0.9 | 6 | False | False | False | False |
| SES-700003 | USR-201832 | 2025-12-08 | mobile_app_ios | 9.9 | 10 | False | False | False | False |
| SES-700004 | USR-202280 | 2025-12-14 | mobile_app_android | 8.3 | 11 | False | False | False | False |
| SES-700005 | USR-201999 | 2025-11-01 | mobile_app_android | 13.2 | 9 | False | False | False | False |
| SES-700006 | USR-200283 | 2026-01-20 | mobile_app_android | 12.6 | 6 | False | False | False | False |
| SES-700007 | USR-201095 | 2026-01-12 | mobile_app_ios | 1.1 | 4 | False | False | False | False |
| SES-700008 | USR-200889 | 2025-12-07 | mobile_app_ios | 12.6 | 8 | False | False | False | False |
| SES-700009 | USR-201081 | 2025-11-10 | mobile_app_ios | 3 | 7 | False | False | False | False |
| SES-700010 | USR-202730 | 2026-01-07 | mobile_app_ios | 0.8 | 11 | False | False | False | False |
| SES-700011 | USR-200441 | 2026-04-27 | mobile_app_android | 12 | 10 | True | False | False | False |
| SES-700012 | USR-200770 | 2025-11-08 | mobile_app_android | 1.9 | 10 | False | False | False | False |
| SES-700013 | USR-202630 | 2025-12-26 | mobile_app_android | 13.1 | 6 | False | False | False | False |
| SES-700014 | USR-202250 | 2025-12-04 | mobile_app_android | 9.1 | 12 | False | False | False | False |
| SES-700015 | USR-202230 | 2026-02-10 | mobile_app_ios | 14 | 3 | False | False | False | False |
| SES-700016 | USR-202020 | 2026-01-11 | mobile_app_ios | 9.7 | 6 | False | False | False | False |
| SES-700017 | USR-201799 | 2026-01-06 | mobile_app_ios | 2.6 | 5 | True | False | False | False |
| SES-700018 | USR-201040 | 2025-12-13 | mobile_app_android | 0.6 | 8 | True | False | False | False |
| SES-700019 | USR-202853 | 2026-04-29 | mobile_app_android | 4.5 | 8 | False | False | False | False |
| SES-700020 | USR-202229 | 2026-03-20 | mobile_app_android | 4.5 | 2 | False | False | False | False |
| SES-700021 | USR-201668 | 2026-02-20 | mobile_app_android | 12.8 | 6 | False | False | False | False |
| SES-700022 | USR-200341 | 2025-11-18 | mobile_app_ios | 1 | 3 | True | False | False | False |
| SES-700023 | USR-202882 | 2025-11-30 | mobile_app_android | 1.9 | 8 | True | False | False | False |
| SES-700024 | USR-200414 | 2025-11-06 | mobile_app_ios | 11.5 | 11 | False | False | False | False |
| SES-700025 | USR-202950 | 2025-12-12 | mobile_app_ios | 2.9 | 8 | False | False | False | False |
| SES-700026 | USR-201920 | 2025-12-17 | mobile_app_android | 2.6 | 8 | True | False | False | False |
| SES-700027 | USR-200277 | 2025-11-09 | mobile_app_android | 2.3 | 6 | True | False | False | False |
| SES-700028 | USR-201216 | 2025-11-02 | mobile_app_android | 11.3 | 10 | False | False | False | False |
... [truncated, 2371 more rows]
