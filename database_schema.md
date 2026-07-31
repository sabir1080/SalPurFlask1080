# Complete Database Schema

**Total Tables: 46**

## ACCOUNT
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| code | VARCHAR(20) | No | — |
| name | VARCHAR(100) | No | — |
| type | VARCHAR(20) | No | — |
| parent_id | INTEGER | Yes | — |
| is_group | BOOLEAN | No | — |
| is_control | BOOLEAN | No | — |
| is_active | BOOLEAN | No | — |
| cash_flow_section | VARCHAR(12) | Yes | — |
| role | VARCHAR(30) | Yes | — |

## ACCOUNTING_PERIOD
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| fiscal_year_id | INTEGER | No | — |
| name | VARCHAR(40) | No | — |
| start_date | DATE | No | — |
| end_date | DATE | No | — |
| is_closed | BOOLEAN | No | — |

## AUDIT_LOG
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| created_at | DATETIME | No | — |
| user_id | INTEGER | Yes | — |
| user_name | VARCHAR(100) | No | — |
| action | VARCHAR(20) | No | — |
| entity | VARCHAR(50) | No | — |
| entity_id | INTEGER | Yes | — |
| summary | VARCHAR(300) | No | — |

## BUSINESS_CATEGORY
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| name | VARCHAR(100) | No | — |
| slug | VARCHAR(50) | No | — |
| description | TEXT | Yes | — |
| icon | VARCHAR(50) | Yes | — |
| is_enabled | BOOLEAN | Yes | — |
| priority | INTEGER | Yes | — |
| color | VARCHAR(20) | Yes | — |
| config_data | JSON | Yes | — |
| created_at | DATETIME | Yes | — |
| updated_at | DATETIME | Yes | — |

## CATEGORY
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| name | VARCHAR(100) | No | — |

## CATEGORY_MENU_ITEM
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| category_id | INTEGER | No | — |
| label | VARCHAR(100) | No | — |
| route | VARCHAR(100) | Yes | — |
| icon | VARCHAR(50) | Yes | — |
| parent_id | INTEGER | Yes | — |
| position | INTEGER | Yes | — |
| permissions | JSON | Yes | — |

## CATEGORY_REPORT
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| category_id | INTEGER | No | — |
| name | VARCHAR(100) | No | — |
| slug | VARCHAR(50) | No | — |
| description | TEXT | Yes | — |
| report_type | VARCHAR(20) | Yes | — |
| query_config | JSON | Yes | — |
| columns | JSON | Yes | — |
| filters | JSON | Yes | — |

## CATEGORY_VALIDATION
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| category_id | INTEGER | No | — |
| rule_name | VARCHAR(100) | No | — |
| rule_type | VARCHAR(50) | Yes | — |
| field_name | VARCHAR(100) | Yes | — |
| condition | JSON | Yes | — |
| error_message | VARCHAR(300) | No | — |
| is_active | BOOLEAN | Yes | — |

## CONFIGURATION_SNAPSHOT
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| timestamp | DATETIME | Yes | — |
| enabled_categories | JSON | Yes | — |
| configuration_hash | VARCHAR(64) | Yes | — |
| changed_by | INTEGER | Yes | — |
| description | TEXT | Yes | — |

## CUSTOMER
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| name | VARCHAR(100) | No | — |
| contact | VARCHAR(15) | No | — |
| address | VARCHAR(200) | No | — |
| opening_balance | NUMERIC(14, 4) | No | — |

## CUSTOMER_LEDGER_ENTRY
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| customer_id | INTEGER | No | — |
| entry_date | DATETIME | No | — |
| entry_type | VARCHAR(30) | No | — |
| source_type | VARCHAR(20) | No | — |
| source_id | INTEGER | Yes | — |
| description | VARCHAR(300) | No | — |
| debit | NUMERIC(14, 4) | No | — |
| credit | NUMERIC(14, 4) | No | — |
| balance_after | NUMERIC(14, 4) | No | — |

## CUSTOMER_PAYMENT
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| customer_id | INTEGER | No | — |
| sale_id | INTEGER | Yes | — |
| amount | NUMERIC(14, 4) | No | — |
| payment_date | DATETIME | No | — |
| payment_method | VARCHAR(20) | No | — |
| account_id | INTEGER | Yes | — |
| reference_no | VARCHAR(100) | Yes | — |
| notes | VARCHAR(300) | Yes | — |
| is_reversed | BOOLEAN | No | — |
| reversed_at | DATETIME | Yes | — |

## DELIVERY_CHALLAN
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| sale_id | INTEGER | No | — |
| challan_date | DATETIME | No | — |
| dispatch_date | DATETIME | Yes | — |
| delivery_date | DATETIME | Yes | — |
| status | VARCHAR(20) | No | — |
| transport | VARCHAR(100) | Yes | — |
| notes | VARCHAR(300) | Yes | — |

## DEPRECIATION_CHARGE
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| asset_id | INTEGER | No | — |
| period_end | DATETIME | No | — |
| amount | NUMERIC(14, 4) | No | — |
| entry_id | INTEGER | Yes | — |

## DOCUMENT_SEQUENCE
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| doc_type | VARCHAR(20) | No | — |
| year | VARCHAR(40) | No | — |
| prefix | VARCHAR(10) | No | — |
| next_number | INTEGER | No | — |

## EXPENSE
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| category_id | INTEGER | Yes | — |
| description | VARCHAR(300) | No | — |
| amount | NUMERIC(14, 4) | No | — |
| date | DATETIME | No | — |
| payment_method | VARCHAR(20) | No | — |
| account_id | INTEGER | Yes | — |
| reference_no | VARCHAR(100) | Yes | — |
| is_reversed | BOOLEAN | No | — |
| reversed_at | DATETIME | Yes | — |
| notes | VARCHAR(300) | Yes | — |

## EXPENSE_CATEGORY
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| name | VARCHAR(100) | No | — |
| gl_account_id | INTEGER | Yes | — |

## FINANCIAL_ACCOUNT
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| name | VARCHAR(80) | No | — |
| method | VARCHAR(20) | No | — |
| account_type | VARCHAR(10) | No | — |
| opening_balance | NUMERIC(14, 4) | No | — |
| is_active | BOOLEAN | No | — |
| is_control | BOOLEAN | No | — |
| parent_id | INTEGER | Yes | — |
| gl_account_id | INTEGER | Yes | — |

## FISCAL_YEAR
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| name | VARCHAR(40) | No | — |
| start_date | DATE | No | — |
| end_date | DATE | No | — |
| is_closed | BOOLEAN | No | — |
| closed_at | DATETIME | Yes | — |

## FIXED_ASSET
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| name | VARCHAR(120) | No | — |
| tag | VARCHAR(40) | Yes | — |
| acquisition_date | DATETIME | No | — |
| cost | NUMERIC(14, 4) | No | — |
| salvage_value | NUMERIC(14, 4) | No | — |
| method | VARCHAR(20) | No | — |
| useful_life_months | INTEGER | Yes | — |
| rate_percent | NUMERIC(14, 4) | Yes | — |
| status | VARCHAR(20) | No | — |
| disposal_date | DATETIME | Yes | — |
| disposal_proceeds | NUMERIC(14, 4) | Yes | — |
| notes | VARCHAR(300) | Yes | — |

## IMPORT_LOG
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| created_at | DATETIME | No | — |
| user_id | INTEGER | No | — |
| import_type | VARCHAR(50) | No | — |
| file_name | VARCHAR(255) | No | — |
| file_type | VARCHAR(10) | No | — |
| total_records | INTEGER | No | — |
| successful | INTEGER | No | — |
| failed | INTEGER | No | — |
| status | VARCHAR(20) | No | — |
| errors | TEXT | Yes | — |

## ITEM
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| name | VARCHAR(100) | No | — |
| category_id | INTEGER | Yes | — |
| business_category_id | INTEGER | Yes | — |
| unit | VARCHAR(20) | No | — |
| barcode | VARCHAR(64) | Yes | — |
| opening_stock | INTEGER | No | — |
| stock | INTEGER | No | — |
| reorder_level | INTEGER | No | — |
| purchase_price | NUMERIC(14, 4) | Yes | — |
| sale_price | NUMERIC(14, 4) | Yes | — |
| inventory_value | NUMERIC(14, 4) | No | — |
| default_tax_percent | DECIMAL(5, 2) | Yes | 0.0 |
| is_taxable | BOOLEAN | Yes | TRUE |

## ITEM_UNIT
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| item_id | INTEGER | No | — |
| name | VARCHAR(20) | No | — |
| factor | INTEGER | No | — |
| purchase_price | NUMERIC(14, 4) | Yes | — |
| sale_price | NUMERIC(14, 4) | Yes | — |

## JOURNAL_ENTRY
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| entry_date | DATETIME | No | — |
| reference | VARCHAR(50) | Yes | — |
| description | VARCHAR(300) | No | — |
| source_type | VARCHAR(20) | No | — |
| source_id | INTEGER | Yes | — |
| period_id | INTEGER | Yes | — |
| reversal_of_id | INTEGER | Yes | — |
| is_reversed | BOOLEAN | No | — |
| created_by_id | INTEGER | Yes | — |
| created_at | DATETIME | No | — |

## JOURNAL_LINE
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| entry_id | INTEGER | No | — |
| account_id | INTEGER | No | — |
| debit | NUMERIC(14, 4) | No | — |
| credit | NUMERIC(14, 4) | No | — |
| memo | VARCHAR(200) | Yes | — |

## POS_HOLD
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| customer_id | INTEGER | No | — |
| user_id | INTEGER | No | — |
| cart_data | TEXT | No | — |
| hold_time | DATETIME | No | — |
| last_modified | DATETIME | Yes | — |
| notes | VARCHAR(300) | Yes | — |
| account_id | INTEGER | Yes | — |
| amount_paid_memo | NUMERIC(14, 4) | Yes | — |
| status | VARCHAR(20) | No | — |
| version | INTEGER | No | 1 |

## PRODUCT_CATEGORY_DATA
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| product_id | INTEGER | No | — |
| category_id | INTEGER | No | — |
| field_name | VARCHAR(100) | No | — |
| field_value | JSON | Yes | — |
| created_at | DATETIME | Yes | — |
| updated_at | DATETIME | Yes | — |

## PRODUCT_FIELD
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| category_id | INTEGER | No | — |
| field_name | VARCHAR(100) | No | — |
| field_label | VARCHAR(100) | No | — |
| field_type | VARCHAR(20) | No | — |
| is_required | BOOLEAN | Yes | — |
| is_searchable | BOOLEAN | Yes | — |
| is_filterable | BOOLEAN | Yes | — |
| placeholder | VARCHAR(200) | Yes | — |
| help_text | VARCHAR(300) | Yes | — |
| validation_pattern | VARCHAR(500) | Yes | — |
| min_value | NUMERIC | Yes | — |
| max_value | NUMERIC | Yes | — |
| options | JSON | Yes | — |
| default_value | VARCHAR(200) | Yes | — |
| position | INTEGER | Yes | — |
| tab_name | VARCHAR(50) | Yes | — |
| created_at | DATETIME | Yes | — |

## PURCHASE
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| supplier_id | INTEGER | No | — |
| item_id | INTEGER | No | — |
| quantity | INTEGER | No | — |
| purchase_price | NUMERIC(14, 4) | No | — |
| discount_type | VARCHAR(10) | No | — |
| discount_value | NUMERIC(14, 4) | No | — |
| tax_percent | NUMERIC(14, 4) | No | — |
| discount_amount | NUMERIC(14, 4) | No | — |
| tax_amount | NUMERIC(14, 4) | No | — |
| date | DATETIME | No | — |
| notes | VARCHAR(300) | Yes | — |
| is_reversed | BOOLEAN | No | — |
| reversed_at | DATETIME | Yes | — |
| invoice_no | VARCHAR(30) | Yes | — |

## PURCHASE_ITEM
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| purchase_id | INTEGER | No | — |
| item_id | INTEGER | No | — |
| quantity | INTEGER | No | — |
| purchase_price | NUMERIC(14, 4) | No | — |
| discount_type | VARCHAR(10) | No | — |
| discount_value | NUMERIC(14, 4) | No | — |
| discount_amount | NUMERIC(14, 4) | No | — |
| tax_percent | NUMERIC(14, 4) | No | — |
| tax_amount | NUMERIC(14, 4) | No | — |
| amount | NUMERIC(14, 4) | No | — |
| unit_name | VARCHAR(20) | Yes | — |
| unit_factor | INTEGER | No | — |

## PURCHASE_ORDER
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| supplier_id | INTEGER | No | — |
| order_date | DATETIME | No | — |
| expected_date | DATETIME | Yes | — |
| status | VARCHAR(20) | No | — |
| notes | VARCHAR(300) | Yes | — |
| converted_purchase_id | INTEGER | Yes | — |

## PURCHASE_ORDER_ITEM
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| po_id | INTEGER | No | — |
| item_id | INTEGER | No | — |
| quantity | INTEGER | No | — |
| purchase_price | NUMERIC(14, 4) | No | — |
| unit_name | VARCHAR(20) | Yes | — |
| unit_factor | INTEGER | No | — |

## PURCHASE_RETURN
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| purchase_id | INTEGER | No | — |
| supplier_id | INTEGER | No | — |
| item_id | INTEGER | No | — |
| quantity | INTEGER | No | — |
| return_price | NUMERIC(14, 4) | No | — |
| date | DATETIME | No | — |
| reason | VARCHAR(300) | Yes | — |
| cost_removed | NUMERIC(14, 4) | No | — |
| is_reversed | BOOLEAN | No | — |
| reversed_at | DATETIME | Yes | — |
| unit_name | VARCHAR(20) | Yes | — |
| unit_factor | INTEGER | No | — |
| purchase_item_id | INTEGER | Yes | — |

## QUOTATION
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| customer_id | INTEGER | No | — |
| quote_date | DATETIME | No | — |
| valid_until | DATETIME | Yes | — |
| status | VARCHAR(20) | No | — |
| notes | VARCHAR(300) | Yes | — |
| converted_sale_id | INTEGER | Yes | — |

## QUOTATION_ITEM
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| quotation_id | INTEGER | No | — |
| item_id | INTEGER | No | — |
| quantity | INTEGER | No | — |
| sale_price | NUMERIC(14, 4) | No | — |
| discount_type | VARCHAR(10) | No | — |
| discount_value | NUMERIC(14, 4) | No | — |
| tax_percent | NUMERIC(14, 4) | No | — |
| unit_name | VARCHAR(20) | Yes | — |
| unit_factor | INTEGER | No | — |

## RATE_LIMIT_HIT
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| key | VARCHAR(200) | No | — |
| created_at | DATETIME | No | — |

## SALE
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| customer_id | INTEGER | No | — |
| item_id | INTEGER | No | — |
| quantity | INTEGER | No | — |
| sale_price | NUMERIC(14, 4) | No | — |
| cost_price | NUMERIC(14, 4) | No | — |
| discount_type | VARCHAR(10) | No | — |
| discount_value | NUMERIC(14, 4) | No | — |
| tax_percent | NUMERIC(14, 4) | No | — |
| discount_amount | NUMERIC(14, 4) | No | — |
| tax_amount | NUMERIC(14, 4) | No | — |
| date | DATETIME | No | — |
| notes | VARCHAR(300) | Yes | — |
| is_reversed | BOOLEAN | No | — |
| reversed_at | DATETIME | Yes | — |
| invoice_no | VARCHAR(30) | Yes | — |

## SALE_ITEM
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| sale_id | INTEGER | No | — |
| item_id | INTEGER | No | — |
| quantity | INTEGER | No | — |
| sale_price | NUMERIC(14, 4) | No | — |
| cost_price | NUMERIC(14, 4) | No | — |
| discount_type | VARCHAR(10) | No | — |
| discount_value | NUMERIC(14, 4) | No | — |
| discount_amount | NUMERIC(14, 4) | No | — |
| tax_percent | NUMERIC(14, 4) | No | — |
| tax_amount | NUMERIC(14, 4) | No | — |
| amount | NUMERIC(14, 4) | No | — |
| unit_name | VARCHAR(20) | Yes | — |
| unit_factor | INTEGER | No | — |

## SALE_RETURN
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| sale_id | INTEGER | No | — |
| customer_id | INTEGER | No | — |
| item_id | INTEGER | No | — |
| quantity | INTEGER | No | — |
| return_price | NUMERIC(14, 4) | No | — |
| date | DATETIME | No | — |
| reason | VARCHAR(300) | Yes | — |
| cost_restored | NUMERIC(14, 4) | No | — |
| is_reversed | BOOLEAN | No | — |
| reversed_at | DATETIME | Yes | — |
| unit_name | VARCHAR(20) | Yes | — |
| unit_factor | INTEGER | No | — |
| sale_item_id | INTEGER | Yes | — |

## STOCK_ADJUSTMENT
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| item_id | INTEGER | No | — |
| adj_type | VARCHAR(30) | No | — |
| quantity | INTEGER | No | — |
| direction | VARCHAR(4) | No | — |
| date | DATETIME | No | — |
| reason | VARCHAR(300) | Yes | — |
| cost_value | NUMERIC(14, 4) | No | — |
| is_reversed | BOOLEAN | No | — |
| reversed_at | DATETIME | Yes | — |

## SUPPLIER
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| name | VARCHAR(100) | No | — |
| contact | VARCHAR(15) | No | — |
| address | VARCHAR(200) | No | — |
| opening_balance | NUMERIC(14, 4) | No | — |

## SUPPLIER_LEDGER_ENTRY
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| supplier_id | INTEGER | No | — |
| entry_date | DATETIME | No | — |
| entry_type | VARCHAR(30) | No | — |
| source_type | VARCHAR(20) | No | — |
| source_id | INTEGER | Yes | — |
| description | VARCHAR(300) | No | — |
| debit | NUMERIC(14, 4) | No | — |
| credit | NUMERIC(14, 4) | No | — |
| balance_after | NUMERIC(14, 4) | No | — |

## SUPPLIER_PAYMENT
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| supplier_id | INTEGER | No | — |
| purchase_id | INTEGER | Yes | — |
| amount | NUMERIC(14, 4) | No | — |
| payment_date | DATETIME | No | — |
| payment_method | VARCHAR(20) | No | — |
| account_id | INTEGER | Yes | — |
| reference_no | VARCHAR(100) | Yes | — |
| notes | VARCHAR(300) | Yes | — |
| is_reversed | BOOLEAN | No | — |
| reversed_at | DATETIME | Yes | — |

## TAX_CODE
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| name | VARCHAR(60) | No | — |
| is_active | BOOLEAN | No | — |

## TAX_COMPONENT
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| tax_code_id | INTEGER | No | — |
| name | VARCHAR(40) | No | — |
| rate | NUMERIC(7, 4) | No | — |
| input_account_id | INTEGER | No | — |
| output_account_id | INTEGER | No | — |

## USER
| Column Name | Data Type | Nullable | Default |
|---|---|---|---|
| id | INTEGER | No | — |
| name | VARCHAR(100) | No | — |
| email | VARCHAR(120) | No | — |
| password | VARCHAR(255) | No | — |
| verified | BOOLEAN | No | — |
| role | VARCHAR(20) | No | — |
| reset_token | VARCHAR(120) | Yes | — |
| reset_token_expiry | DATETIME | Yes | — |

