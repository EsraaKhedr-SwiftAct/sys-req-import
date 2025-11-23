## **1.Handling `.reqif` Files From Different Vendors**

Below is a detailed comparison showing how major vendors structure ReqIF files and how this tool handles their differences.

---

## 📊 Vendor Comparison Table

| Vendor / Tool        | Attribute Definitions                                                                  | XHTML Handling                            | ENUM Behavior                                       | Hierarchy Style                                         | Special Notes                                                           |
| -------------------- | -------------------------------------------------------------------------------------- | ----------------------------------------- | --------------------------------------------------- | ------------------------------------------------------- | ----------------------------------------------------------------------- |
| **IBM DOORS Next**   | Uses GUID-based IDs, sometimes lowercase `identifier`; ENUMs stored in datatype blocks | Rich XHTML, often nested deeply           | ENUM labels may appear in `<THE-VALUE>` or as GUIDs | Standard `SPEC-HIERARCHY` but may embed custom metadata | Very inconsistent ENUM format; tool fully supports all detection layers |
| **Siemens Polarion** | Often puts definition ID as XML attribute `ATTRIBUTE-DEFINITION`                       | Clean XHTML but may inline plain text     | ENUM stored as direct ID or full label              | Simple hierarchy                                        | Some blocks use lowercase tags; parser handles case variances           |
| **Jama Connect**     | Uses LONG-NAME heavily; IDs stable                                                     | XHTML usually minimal; descriptions clean | ENUM sometimes provided directly as text            | Flat hierarchy with few levels                          | Jama’s ReqIF often omits nested <VALUES>; supported by fallback logic   |
| **Sparx EA**         | Uses clean IDENTIFIER & LONG-NAME                                                      | XHTML rarely used                         | ENUM standard                                       | Hierarchy usually complete                              | EA outputs very clean ReqIF; easiest format to parse                    |
| **ReqIF Studio**     | Very strict ReqIF standard                                                             | Perfect XHTML                             | ENUM via `<ENUM-VALUE-REF>`                         | Full hierarchy                                          | Baseline for testing; parser natively aligned                           |

---

## Additional Details Per Vendor

### **IBM DOORS Next**

* ENUMs may be defined in *either* `SPEC-ENUMERATION-VALUE` or inside datatype blocks → parser merges all.
* `<THE-VALUE>` sometimes contains raw label instead of ref ID → tool detects both.
* XHTML contains deeply nested nodes → parser flattens with `clean_xhtml_to_text()`.
* Uses lowercase attributes like `identifier` → parser checks both uppercase/lowercase.
* Duplicate SPEC-OBJECT entries appear → tool merges using "most complete wins" logic.

### **Siemens Polarion**

* Often uses attribute-def in XML attribute, e.g., `<ATTRIBUTE-VALUE-STRING ATTRIBUTE-DEFINITION="AD_TITLE">` → supported.
* Some fields appear outside `<VALUES>` container → parser searches both locations.
* ENUMs sometimes encoded as direct text → resolved.

### **Jama Connect**

* May exclude `<VALUES>` and put attribute values directly under SPEC-OBJECT → parser supports.
* Descriptions are often XHTML-lite → cleaned properly.
* Uses long descriptive names → auto-mapped.

### **ReqIF Studio**

* Fully standard-compliant.
* Cleanest export; used as baseline testing.

### **Enterprise Architect (EA)**

* Produces nearly spec-perfect ReqIF.
* ENUM mapping straightforward.
* Hierarchy always provided.

---