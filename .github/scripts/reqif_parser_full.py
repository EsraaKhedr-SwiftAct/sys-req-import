#!/usr/bin/env python3
"""
reqif_parser_full.py (Debug-enabled)

Includes verbose debugging to trace title/description detection problems,
and now correctly extracts Enumeration, Integer, Boolean attribute values,
multi-enum lists, spec hierarchy, relations, vendor fallbacks and extensions,
+ Phase 2 enhancements: namespace-agnostic parsing, enum-id resolution,
XHTML flattening (to plain text with line breaks), and optional attachment extraction.
"""

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional
import zipfile
import tempfile
import os
import base64
import re

# -----------------------
# Helper utilities
# -----------------------
def _load_reqif_or_reqifz(path: str) -> str:
    """If path is .reqifz, extract and return internal .reqif path, otherwise return path."""
    if not path:
        return path
    p = path
    if path.lower().endswith(".reqifz"):
        tmp = tempfile.mkdtemp(prefix="reqif_extract_")
        with zipfile.ZipFile(path, "r") as z:
            # find first .reqif file inside
            reqif_name = None
            for nm in z.namelist():
                if nm.lower().endswith(".reqif"):
                    reqif_name = nm
                    break
            if not reqif_name:
                raise ValueError("No .reqif file found inside .reqifz archive")
            z.extract(reqif_name, tmp)
            return os.path.join(tmp, reqif_name)
    return p


def local_tag(el):
    """Return local-name of an element (namespace-agnostic)."""
    if not isinstance(el.tag, str):
        return str(el.tag)
    return el.tag.split("}")[-1]


def iter_elements_by_local_name(root, local_name):
    """Yield elements whose local-name() matches local_name (namespace-agnostic)."""
    for el in root.iter():
        try:
            if local_tag(el) == local_name:
                yield el
        except Exception:
            continue


def find_first_child_local(parent, tag_name):
    """Return first child with matching local-name or None."""
    if parent is None:
        return None
    for c in list(parent):
        if local_tag(c) == tag_name:
            return c
    return None


def text_of(elem):
    if elem is None:
        return ""
    return (elem.text or "").strip()


def clean_xhtml_to_text(elem):
    """
    Flatten XHTML node to plain text while preserving paragraph and line breaks.
    Behavior: convert <p>, <div> and <br> into newlines, normalize whitespace,
    and preserve line breaks (Option 1).
    """
    if elem is None:
        return ""

    parts: List[str] = []

    def rec(n):
        tag = local_tag(n).lower()
        # Start paragraph/div with newline
        if tag in ("p", "div"):
            parts.append("\n")
        # node text
        if n.text:
            parts.append(n.text)
        # children
        for c in list(n):
            rec(c)
            # tail text after child
            if c.tail:
                parts.append(c.tail)
        # <br/> mapped to newline
        if tag == "br":
            parts.append("\n")
        # end paragraph/div with newline
        if tag in ("p", "div"):
            parts.append("\n")

    rec(elem)
    txt = "".join(parts)
    # Replace any sequences of whitespace with single spaces except newlines; collapse multiple newlines
    # Normalize whitespace but keep line breaks:
    #  - convert sequences of spaces/tabs to single space
    #  - collapse 3+ newlines to 2
    txt = re.sub(r'[ \t\f\v]+', ' ', txt)
    txt = re.sub(r'\r\n?', '\n', txt)
    txt = re.sub(r'\n\s*\n\s*\n+', '\n\n', txt)  # keep up to double newline
    # strip spaces at line ends and ends of text
    lines = [line.rstrip() for line in txt.splitlines()]
    cleaned = "\n".join([ln.strip() for ln in lines if ln.strip() != ""])
    return cleaned.strip()


# -----------------------
# Core classes
# -----------------------
class ReqIFRequirement:
    def __init__(self, identifier, title="", description="", attributes=None):
        self.id = identifier
        self.title = title
        self.description = description
        self.attributes = attributes or {}

    def __repr__(self):
        return f"ReqIFRequirement(id={self.id!r}, title={self.title!r})"


class ReqIFParser:
    REQIF_NS = {
        "reqif": "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd",
        "xhtml": "http://www.w3.org/1999/xhtml",
    }

    def __init__(self, filename, normalize_types: bool = True, preserve_extensions: bool = True,
                 extract_attachments: bool = False):
        print(f"🔍 Loading ReqIF file: {filename}")
        # support .reqifz transparently
        path_to_parse = _load_reqif_or_reqifz(filename)
        self.filename = filename
        self.tree = ET.parse(path_to_parse)
        self.root = self.tree.getroot()
        self.ns = self._detect_ns()
        print(f"✅ Detected namespace map: {self.ns}")

        # Behavior flags
        self.normalize_types = normalize_types
        self.preserve_extensions = preserve_extensions
        self.extract_attachments = extract_attachments

        # --- NEW/UPDATED INITIALIZATION ---
        # Maps: ATTR_DEF_ID -> LONG-NAME (human readable)
        self.def_map: Dict[str, str] = self._build_definition_map()

        # Maps: ENUM_ID -> LONG-NAME
        self.enum_map: Dict[str, str] = self._build_enum_map()

        # Spec-object-type map: TYPE_IDENTIFIER -> list of attribute def ids (optional)
        self.spec_object_types = self._build_spec_object_type_map()

        # Hierarchy and relations containers (populated in parse)
        self.hierarchy_map = {}  # object_id -> [child_object_ids]
        self.parent_map = {}     # object_id -> parent_object_id
        self.relations: List[Dict[str, Any]] = []

        # Attachments storage when extract_attachments True
        self.attachments: Dict[str, List[Dict[str, Any]]] = {}

        print(f"✅ Definition map built ({len(self.def_map)} items):")
        for k, v in self.def_map.items():
            print(f"   - {k} → {v}")
        print(f"✅ Enumeration map built ({len(self.enum_map)} items).")
        # ----------------------------------

    # ----------------------------------------------------------
    # Namespace-agnostic find helpers
    # ----------------------------------------------------------
    def _find(self, element, tag):
        """Namespace-agnostic single find: finds first descendant with local-name == tag."""
        if element is None:
            return None
        # try namespace-aware first
        try:
            res = element.find(f".//reqif:{tag}", self.ns)
            if res is not None:
                return res
        except Exception:
            pass
        # fallback: local-name searching
        for el in element.iter():
            if local_tag(el) == tag:
                return el
        return None

    def _findall(self, element, tag):
        """Namespace-agnostic findall: finds all descendants with local-name == tag."""
        if element is None:
            return []
        try:
            res = element.findall(f".//reqif:{tag}", self.ns)
            if res:
                return res
        except Exception:
            pass
        return list(iter_elements_by_local_name(element, tag))

    # ----------------------------------------------------------
    # Detects if file uses default namespace or prefixes
    # ----------------------------------------------------------
    def _detect_ns(self):
        tag = self.root.tag
        if isinstance(tag, str) and tag.startswith("{"):
            uri = tag.split("}")[0].strip("{}")
            return {"reqif": uri, "xhtml": "http://www.w3.org/1999/xhtml"}
        return self.REQIF_NS

    # ----------------------------------------------------------
    # Build map of attribute definitions (robust)
    # ----------------------------------------------------------
    def _build_definition_map(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}

        # We attempt to capture common attribute definition tags
        def_types = [
            "ATTRIBUTE-DEFINITION-STRING",
            "ATTRIBUTE-DEFINITION-XHTML",
            "ATTRIBUTE-DEFINITION-ENUMERATION",
            "ATTRIBUTE-DEFINITION-INTEGER",
            "ATTRIBUTE-DEFINITION-BOOLEAN",
            "ATTRIBUTE-DEFINITION-DATE",
            "ATTRIBUTE-DEFINITION-REAL",
        ]

        # Try namespace-aware search first; then fallback to local-name iteration
        for def_type in def_types:
            found = self.root.findall(f".//reqif:{def_type}", self.ns)
            if not found:
                found = list(iter_elements_by_local_name(self.root, def_type))
            if found:
                print(f"📦 Found {len(found)} {def_type} elements")
            for attr_def in found:
                def_id = attr_def.get("IDENTIFIER") or attr_def.get("ID")
                # fallback long name resolution
                long_name = attr_def.get("LONG-NAME", "") or attr_def.get("DESC", "") or ""
                # Some vendors omit LONG-NAME; fallback to IDENTIFIER or ALTERNATIVE-ID if present
                if not long_name:
                    # look for ALTERNATIVE-ID child or attribute (namespace-agnostic)
                    alt = attr_def.find("reqif:ALTERNATIVE-ID", self.ns) or find_first_child_local(attr_def, "ALTERNATIVE-ID")
                    if alt is not None and alt.get("IDENTIFIER"):
                        long_name = alt.get("IDENTIFIER")
                if not long_name:
                    long_name = def_id or ""

                # store under IDENTIFIER if available
                if def_id:
                    mapping[def_id] = long_name

                # also index by LONG-NAME to help fuzzy lookups later
                if long_name:
                    mapping[long_name] = long_name

        return mapping

    # --- NEW METHOD: Maps Enumeration IDs to their Long Names ---
    def _build_enum_map(self) -> Dict[str, str]:
        """
        Build a map of ENUM ID → Label that works across:
        - ReqIF Studio
        - Polarion
        - Jama
        - DOORS Next (GUID-based ENUM-REFs)
        """
        enum_mapping: Dict[str, str] = {}

        # 1) Standard case: SPEC-ENUMERATION-VALUE elements
        enum_vals = self.root.findall(".//reqif:SPEC-ENUMERATION-VALUE", self.ns)
        if not enum_vals:
            enum_vals = list(iter_elements_by_local_name(self.root, "SPEC-ENUMERATION-VALUE"))

        for ev in enum_vals:
            enum_id = ev.get("IDENTIFIER") or ev.get("ID")
            long_name = ev.get("LONG-NAME") or text_of(find_first_child_local(ev, "LONG-NAME"))

            # DOORS Next: label sometimes stored in THE-VALUE instead of LONG-NAME
            if not long_name:
                the_value = find_first_child_local(ev, "THE-VALUE")
                if the_value is not None and (the_value.text or "").strip():
                    long_name = the_value.text.strip()

            if enum_id:
                enum_mapping[enum_id] = long_name or enum_id

        # 2) DOORS Next extra case:
        # ENUM definitions sometimes appear inside DATATYPE-DEFINITION-ENUMERATION → SPECIFIED-VALUES
        dt_defs = self.root.findall(".//reqif:DATATYPE-DEFINITION-ENUMERATION", self.ns)
        if not dt_defs:
            dt_defs = list(iter_elements_by_local_name(self.root, "DATATYPE-DEFINITION-ENUMERATION"))

        for dt in dt_defs:
            specified = dt.find("reqif:SPECIFIED-VALUES", self.ns) or find_first_child_local(dt, "SPECIFIED-VALUES")
            if specified is None:
                continue
            for ev in list(specified):
                enum_id = ev.get("IDENTIFIER") or ev.get("ID")
                long_name = ev.get("LONG-NAME") or text_of(find_first_child_local(ev, "LONG-NAME"))
                if not long_name:
                    the_val = find_first_child_local(ev, "THE-VALUE")
                    if the_val is not None and (the_val.text or "").strip():
                        long_name = the_val.text.strip()
                if enum_id:
                    enum_mapping[enum_id] = long_name or enum_id

        print(f"✅ Enumeration map updated (supports DOORS Next): {len(enum_mapping)} items")
        return enum_mapping

        # ------------------------------------------------------------
    # Build SPEC-OBJECT-TYPE → attribute-definition mapping
    # (Used by Polarion, Jama, DOORS Next)
    # ------------------------------------------------------------
    def _build_spec_object_type_map(self):
        """
        Returns a mapping:
            TYPE_IDENTIFIER → list of ATTRIBUTE-DEFINITION identifiers

        Example returned:
        {
            "REQ_TYPE_SYSTEM": ["TITLE_DEF", "DESC_DEF", "PRIORITY_DEF"],
            "REQ_TYPE_FUNCTIONAL": ["TITLE_DEF", "DESC_DEF"]
        }

        Optional in ReqIF. Only used when tools embed type-level constraints.
        """
        type_map = {}

        # Find all SPEC-OBJECT-TYPE blocks (namespace-agnostic)
        type_elements = self.root.findall(".//reqif:SPEC-OBJECT-TYPE", self.ns)
        if not type_elements:
            type_elements = list(iter_elements_by_local_name(self.root, "SPEC-OBJECT-TYPE"))

        if not type_elements:
            return type_map  # no type metadata → normal

        print(f"🧩 Found {len(type_elements)} SPEC-OBJECT-TYPE elements")

        for t in type_elements:
            type_id = t.get("IDENTIFIER") or t.get("ID")
            if not type_id:
                continue

            attr_defs = []

            # Find ATTRIBUTE-DEFINITIONS list inside this type
            defs_block = t.find("reqif:SPEC-ATTRIBUTES", self.ns) or find_first_child_local(t, "SPEC-ATTRIBUTES")
            if defs_block is not None:
                for ad in list(defs_block):
                    # Attribute definitions reference their IDs
                    ref = ad.get("IDENTIFIER") or ad.get("ID")
                    if not ref:
                        # look for <ALTERNATIVE-ID>
                        alt = find_first_child_local(ad, "ALTERNATIVE-ID")
                        if alt is not None and alt.get("IDENTIFIER"):
                            ref = alt.get("IDENTIFIER")
                    if ref:
                        attr_defs.append(ref)

            # store result
            type_map[type_id] = attr_defs

        return type_map

    # ------------------------------------------------------------
    # Main parse entry
    # ------------------------------------------------------------
    def parse(self) -> List[ReqIFRequirement]:
        reqs: List[ReqIFRequirement] = []
        # Finding REQ-IF-CONTENT: namespace-aware then fallback local-name
        content = self.root.find("reqif:CORE-CONTENT/reqif:REQ-IF-CONTENT", self.ns)
        if content is None:
            # fallback to local-name find
            for c in iter_elements_by_local_name(self.root, "REQ-IF-CONTENT"):
                content = c
                break
        if content is None:
            print("❌ Error: Could not find REQ-IF-CONTENT.")
            return []

        # Build hierarchy map / relations before processing objects (so we can attach)
        self._parse_specifications_and_hierarchy(content)
        self._parse_relations(content)

        # find spec-objects
        spec_objects = content.findall(".//reqif:SPEC-OBJECT", self.ns)
        if not spec_objects:
            spec_objects = list(iter_elements_by_local_name(content, "SPEC-OBJECT"))
        print(f"📄 Found {len(spec_objects)} SPEC-OBJECT elements")

        for spec_obj in spec_objects:
            identifier = spec_obj.get("IDENTIFIER") or spec_obj.get("ID") or "UNKNOWN"
            print(f"\n🔹 Parsing SPEC-OBJECT: {identifier}")

            # --- Collect attributes ---
            attributes = self._collect_attributes(spec_obj)

            # attach hierarchy info if found
            children = self.hierarchy_map.get(identifier, [])
            if children:
                print(f"   → Has children: {children}")
                attributes["__children__"] = children
            parent = self.parent_map.get(identifier)
            if parent:
                attributes["__parent__"] = parent

            # attach relations that mention this object
            related = [r for r in self.relations if r.get("source") == identifier or r.get("target") == identifier]
            if related:
                attributes["__links__"] = related

            # preserve tool-extension raw XML if requested
            if self.preserve_extensions:
                extensions = self._collect_tool_extensions(spec_obj)
                if extensions:
                    attributes["__extensions__"] = extensions

            # attachments: only if extraction enabled
            if self.extract_attachments and identifier in self.attachments:
                attributes["__attachments__"] = self.attachments.get(identifier)

            print(f"   Attributes found: {list(attributes.keys())}")

            title = self._find_flexible(attributes, ["Title", "Name", "Req Title", "Requirement"])
            description = self._find_flexible(attributes, ["Description", "Desc", "Text", "Body", "Content"])
            # Auto-generate title from description if missing
            if not title and description:
                title = self._auto_title_from_description(description)
                print(f"   → Auto-generated Title from Description: {title!r}")
            print(f"   → Detected Title: {title!r}")
            print(f"   → Detected Description: {description!r}")

            reqs.append(ReqIFRequirement(identifier, title, description, attributes))
        return reqs

    # -------------------------------------------------------
    # Collect attribute values with many vendor fallbacks
    # -------------------------------------------------------
    def _collect_attributes(self, spec_obj):
        attrs = {}

        # Search for <VALUES> block (namespace-aware then local)
        values_block = spec_obj.find("reqif:VALUES", self.ns) or spec_obj.find("reqif:values", self.ns)
        if values_block is None:
            # fallback: try by local-name
            for vb in iter_elements_by_local_name(spec_obj, "VALUES"):
                values_block = vb
                break

        # Fallback: some vendors may put ATTRIBUTE-VALUE elements directly under SPEC-OBJECT
        if values_block is None:
            candidates = []
            for tag in [
                "ATTRIBUTE-VALUE-STRING",
                "ATTRIBUTE-VALUE-XHTML",
                "ATTRIBUTE-VALUE-ENUMERATION",
                "ATTRIBUTE-VALUE-INTEGER",
                "ATTRIBUTE-VALUE-BOOLEAN",
                "ATTRIBUTE-VALUE-DATE",
                "ATTRIBUTE-VALUE-REAL",
            ]:
                found = spec_obj.findall(f".//reqif:{tag}", self.ns)
                if not found:
                    found = list(iter_elements_by_local_name(spec_obj, tag))
                candidates.extend(found)
            all_attrs = candidates
        else:
            # Official ReqIF attribute value element names
            value_tags = [
                "ATTRIBUTE-VALUE-STRING",
                "ATTRIBUTE-VALUE-XHTML",
                "ATTRIBUTE-VALUE-ENUMERATION",
                "ATTRIBUTE-VALUE-INTEGER",
                "ATTRIBUTE-VALUE-BOOLEAN",
                "ATTRIBUTE-VALUE-DATE",
                "ATTRIBUTE-VALUE-REAL",
            ]
            all_attrs = []
            for tag in value_tags:
                found = values_block.findall(f"reqif:{tag}", self.ns)
                if not found:
                    found = list(iter_elements_by_local_name(values_block, tag))
                all_attrs += found

        print(f"   → Found {len(all_attrs)} clean ATTRIBUTE-VALUE elements")

        for attr in all_attrs:
            # Acquire attribute definition identifier by several vendor patterns

            # 1) Polarion-style attribute as XML attribute
            ref_id = attr.get("ATTRIBUTE-DEFINITION")

            # 2) Standard nested DEFINITION child: <DEFINITION><ATTRIBUTE-DEFINITION-STRING-REF>AD_TITLE</...>
            if not ref_id:
                def_child = attr.find("reqif:DEFINITION/*", self.ns)
                if def_child is None:
                    # fallback to local-name
                    def_block = find_first_child_local(attr, "DEFINITION")
                    if def_block is not None:
                        for inner in list(def_block):
                            if inner is not None and (inner.text or "").strip():
                                ref_id = (inner.text or "").strip()
                                break
                            if inner is not None:
                                for key in ("REF", "REFID", "ATTRIBUTE-DEFINITION"):
                                    if inner.get(key):
                                        ref_id = inner.get(key).strip()
                                        break
                                if ref_id:
                                    break
                else:
                    if def_child.text and def_child.text.strip():
                        ref_id = def_child.text.strip()

            # 3) Some vendors use a REF attribute on the nested element
            if not ref_id:
                def_elem = attr.find("reqif:DEFINITION", self.ns) or find_first_child_local(attr, "DEFINITION")
                if def_elem is not None:
                    for child in list(def_elem):
                        if child is None:
                            continue
                        if child.text and child.text.strip():
                            ref_id = child.text.strip()
                            break
                        if child.get("REF"):
                            ref_id = child.get("REF").strip()
                            break
                        if child.get("REFID"):
                            ref_id = child.get("REFID").strip()
                            break

            # 4) As a last resort: attempt fuzzy match scanning child text for known def ids
            if not ref_id:
                text_nodes = "".join([(c.text or "") for c in list(attr) if c is not None])
                for known_id in self.def_map.keys():
                    if known_id and known_id in text_nodes:
                        ref_id = known_id
                        break
            if not ref_id:
                # ✅ Phase-2 ENUM / Missing Definition Recovery
                obj_type = spec_obj.get("TYPE") or spec_obj.get("type")
                if obj_type and obj_type in self.spec_object_types:
                    candidate_defs = self.spec_object_types[obj_type]

                    # Try to infer which attribute definition this ENUM belongs to
                    for cand in candidate_defs:
                        if cand in self.def_map:
                            inferred_name = self.def_map.get(cand) or cand
                            print(f"      🔄 Inferred missing attribute definition: {inferred_name} (from type {obj_type})")
                            ref_id = cand
                            attr_name = inferred_name
                            value = self._extract_value(attr)
                            attrs[attr_name] = value
                            break

                if ref_id:
                    continue  # ✅ Re-enter normal attribute processing flow

                # ❗ If still no match → fall back safely
                fallback_key = local_tag(attr)
                val = self._extract_value(attr)

                # ✅ If string and looks like requirement text → treat as Description
                if fallback_key == "ATTRIBUTE-VALUE-STRING":
                    # If there is already a Description, this becomes Title
                    if "Description" not in attrs:
                        print("      🔄 Inferred attribute as Description")
                        attrs["Description"] = val
                    else:
                        print("      🔄 Inferred attribute as Title")
                        attrs["Title"] = val
                    continue

                print(f"      ⚠️ Could not resolve attribute definition; fallback key: {fallback_key}")
                attrs[fallback_key] = val
                continue




            # Get the Attribute's Long Name (if available) or use ref_id
            attr_name = self.def_map.get(ref_id) or ref_id

            # Extract value
            value = self._extract_value(attr)

            # Normalize types optionally
            # NOTE: we added support to normalize list elements as well (enum lists)
            if self.normalize_types:
                if isinstance(value, str):
                    value = self._normalize_type_from_def(ref_id, value)
                elif isinstance(value, list):
                    normed = []
                    for v in value:
                        if isinstance(v, str):
                            normed.append(self._normalize_type_from_def(ref_id, v))
                        else:
                            normed.append(v)
                    value = normed

            if value is None:
                value = ""

            print(f"      DEF={attr_name!r} (ref={ref_id}) → VALUE={value!r}")
            attrs[attr_name] = value

        # If attachments extraction requested, attempt to parse vendor-specific blocks for attachments
        if self.extract_attachments:
            for ext in list(spec_obj):
                t = local_tag(ext)
                if t.upper() in ("TOOL-EXTENSION", "REQIF-TOOL-EXTENSION", "ATTACHMENTS", "BINARY"):
                    for att in list(ext):
                        if local_tag(att).upper() == "ATTACHMENT":
                            name = att.get("NAME") or att.get("name") or att.get("filename")
                            encoding = att.get("ENCODING") or att.get("encoding")
                            data_text = (att.text or "").strip()
                            if data_text:
                                try:
                                    if encoding and encoding.upper() == "BASE64":
                                        b = base64.b64decode(data_text)
                                    else:
                                        b = base64.b64decode(data_text)
                                except Exception:
                                    b = data_text.encode("utf-8", errors="ignore")
                                self.attachments.setdefault(spec_obj.get("IDENTIFIER") or spec_obj.get("ID") or "UNKNOWN", []).append({
                                    "name": name or "attachment",
                                    "data": b,
                                    "encoding": encoding or "BASE64"
                                })

        return attrs

    # -------------------------------------------------------
    # Improved: collect vendor / tool extension blocks safely
    # -------------------------------------------------------
    def _collect_tool_extensions(self, spec_obj) -> Optional[List[str]]:
        if not self.preserve_extensions:
            return None

        extensions = []

        value_container_names = {"VALUES", "values", "ATTRIBUTE-VALUES"}

        attribute_value_tags = {
            "ATTRIBUTE-VALUE-STRING",
            "ATTRIBUTE-VALUE-XHTML",
            "ATTRIBUTE-VALUE-ENUMERATION",
            "ATTRIBUTE-VALUE-INTEGER",
            "ATTRIBUTE-VALUE-BOOLEAN",
            "ATTRIBUTE-VALUE-DATE",
            "ATTRIBUTE-VALUE-REAL",
        }

        for child in spec_obj:
            tag = local_tag(child)
            if tag in value_container_names:
                continue
            if tag in attribute_value_tags:
                continue
            try:
                raw_xml = ET.tostring(child, encoding="unicode")
                if raw_xml.strip():
                    extensions.append(raw_xml)
            except Exception:
                pass

        return extensions if extensions else None

    # -------------------------------------------------------
    # Resolve definition name (kept for backward compatibility)
    # -------------------------------------------------------
    def _resolve_definition_name(self, attr):
        for def_type in [
            "ATTRIBUTE-DEFINITION-STRING-REF",
            "ATTRIBUTE-DEFINITION-XHTML-REF",
            "ATTRIBUTE-DEFINITION-ENUMERATION-REF",
            "ATTRIBUTE-DEFINITION-INTEGER-REF",
            "ATTRIBUTE-DEFINITION-BOOLEAN-REF",
            "ATTRIBUTE-DEFINITION-DATE-REF",
            "ATTRIBUTE-DEFINITION-REAL-REF",
        ]:
            ref_elem = attr.find(f"reqif:DEFINITION/reqif:{def_type}", self.ns)
            if ref_elem is not None and (ref_elem.text or "").strip():
                ref_id = ref_elem.text.strip()
                if ref_id in self.def_map:
                    return self.def_map[ref_id]
                for known_id, long_name in self.def_map.items():
                    if ref_id.lower() in known_id.lower() or known_id.lower() in ref_id.lower():
                        return long_name
            for candidate in iter_elements_by_local_name(attr, def_type):
                if (candidate.text or "").strip():
                    ref_id = candidate.text.strip()
                    if ref_id in self.def_map:
                        return self.def_map[ref_id]
        return None

    # --- UPDATED METHOD: Correctly extracts all value types ---
    def _extract_value(self, attr):
        tag = local_tag(attr)

        # Handle STRING
        if tag == "ATTRIBUTE-VALUE-STRING":
            val = attr.find("reqif:THE-VALUE", self.ns) or find_first_child_local(attr, "THE-VALUE")
            return (val.text or "").strip() if val is not None and val.text else ""

        # Handle XHTML - flatten to plain text with preserved line breaks (Option 1)
        if tag == "ATTRIBUTE-VALUE-XHTML":
            val_elem = attr.find("reqif:THE-VALUE", self.ns) or find_first_child_local(attr, "THE-VALUE")
            if val_elem is not None:
                # prefer xhtml:div if present but fall back to any descendant
                div_elem = val_elem.find("xhtml:div", self.ns) or find_first_child_local(val_elem, "div")
                content = div_elem if div_elem is not None else val_elem
                return clean_xhtml_to_text(content)
            return ""

        # Handle INTEGER/BOOLEAN/DATE (Uses THE-VALUE directly)
        if tag in ["ATTRIBUTE-VALUE-INTEGER", "ATTRIBUTE-VALUE-BOOLEAN", "ATTRIBUTE-VALUE-DATE"]:
            val = attr.find("reqif:THE-VALUE", self.ns) or find_first_child_local(attr, "THE-VALUE")
            return (val.text or "").strip() if val is not None and val.text else ""

        # Handle REAL (float)
        if tag == "ATTRIBUTE-VALUE-REAL":
            val = attr.find("reqif:THE-VALUE", self.ns) or find_first_child_local(attr, "THE-VALUE")
            if val is not None and val.text:
                txt = val.text.strip()
                try:
                    return float(txt) if "." in txt else int(txt)
                except Exception:
                    return txt
            return ""

        # Handle ENUMERATION (multi-ref aware) - ALWAYS RETURN LIST (Option A)
        if tag == "ATTRIBUTE-VALUE-ENUMERATION":
            enum_values: List[str] = []

            # (1) THE-VALUE / ENUM-REF (namespace-aware then local-name)
            enum_refs = attr.findall(".//reqif:THE-VALUE//reqif:ENUM-REF", self.ns)
            if not enum_refs:
                enum_refs = list(iter_elements_by_local_name(attr, "ENUM-REF"))
            for enum_ref in enum_refs:
                if enum_ref is not None and (enum_ref.text or "").strip():
                    enum_id = enum_ref.text.strip()
                    enum_label = self.enum_map.get(enum_id, enum_id)
                    enum_values.append(enum_label)

            # (2) fallback: ENUM-VALUE-REF under VALUES
            if not enum_values:
                enum_refs2 = attr.findall(".//reqif:VALUES//reqif:ENUM-VALUE-REF", self.ns)
                if not enum_refs2:
                    enum_refs2 = list(iter_elements_by_local_name(attr, "ENUM-VALUE-REF"))
                for enum_ref in enum_refs2:
                    if enum_ref is not None and (enum_ref.text or "").strip():
                        enum_id = enum_ref.text.strip()
                        enum_label = self.enum_map.get(enum_id, enum_id)
                        enum_values.append(enum_label)

            # (3) fallback: direct THE-VALUE text; maybe comma-separated
            if not enum_values:
                the = attr.find("reqif:THE-VALUE", self.ns) or find_first_child_local(attr, "THE-VALUE")
                if the is not None and (the.text or "").strip():
                    txt = the.text.strip()
                    if "," in txt:
                        enum_values = [p.strip() for p in txt.split(",") if p.strip()]
                    else:
                        enum_values = [txt]

            # Finalize: ALWAYS return list (even if empty or single)
            return enum_values

        # Fallback: raw text of THE-VALUE
        val = attr.find("reqif:THE-VALUE", self.ns) or find_first_child_local(attr, "THE-VALUE")
        return (val.text or "").strip() if val is not None and val.text else ""

    # ----------------------------------------------------------
    # Type normalizer (optional)
    # ----------------------------------------------------------
    def _normalize_type_from_def(self, def_id: str, value: str):
        # Quick heuristic: inspect def_id or value to guess type
        low = value.strip().lower()

        # boolean variants
        if low in ("true", "false", "yes", "no", "0", "1"):
            return low in ("true", "yes", "1")

        # integer
        try:
            if value.isdigit() or (value.startswith("-") and value[1:].isdigit()):
                return int(value)
        except Exception:
            pass

        # float
        try:
            if "." in value:
                f = float(value)
                return f
        except Exception:
            pass

        # date ISO8601 attempt (handles trailing Z)
        try:
            vs = value.strip()
            if vs.endswith("Z"):
                vs = vs.replace("Z", "+00:00")
            # fromisoformat supports offsets like +00:00
            dt = datetime.fromisoformat(vs)
            return dt
        except Exception:
            pass

        # default: return original string
        return value

    # ----------------------------------------------------------
    # Flexible attribute name search helper
    # ----------------------------------------------------------
    def _find_flexible(self, attributes: Dict[str, Any], keys: List[str]) -> str:
        for k in keys:
            for akey, val in attributes.items():
                if isinstance(akey, str) and k.lower() in akey.lower():
                    # flatten lists to a readable string for title/desc detection
                    if isinstance(val, list):
                        return ", ".join([str(v) for v in val])
                    return str(val)
        return ""

    # ----------------------------------------------------------
    # Auto-generate title from description: first sentence heuristic
    # ----------------------------------------------------------
    def _auto_title_from_description(self, description: str) -> str:
        # simple heuristic: first line or until first period (.)
        if not description:
            return ""
        desc = description.strip()
        lines = [l.strip() for l in desc.splitlines() if l.strip()]
        if lines:
            first = lines[0]
            # split by period but avoid abbreviations (very simple)
            if "." in first:
                cand = first.split(".")[0].strip()
                if len(cand) >= 3:
                    return cand
            # fallback: take first 6-10 words
            words = first.split()
            return " ".join(words[:8]) + ("..." if len(words) > 8 else "")
        return desc[:60]

    # ----------------------------------------------------------
    # Parse specifications and build hierarchy (SPEC-HIERARCHY)
    # ----------------------------------------------------------
    def _parse_specifications_and_hierarchy(self, content_root):
        # Look for SPEC-HIERARCHY elements anywhere (namespace-agnostic)
        for sh in iter_elements_by_local_name(content_root, "SPEC-HIERARCHY"):
            self._walk_spec_hierarchy(sh, parent_id=None)

    def _walk_spec_hierarchy(self, sh_elem, parent_id=None):
        # object ref may be attribute 'OBJECT' or child <OBJECT>id</OBJECT>
        obj_ref = sh_elem.get("OBJECT")
        if not obj_ref:
            obj_child = sh_elem.find("reqif:OBJECT", self.ns) or find_first_child_local(sh_elem, "OBJECT")
            if obj_child is not None and (obj_child.text or "").strip():
                obj_ref = obj_child.text.strip()

        if obj_ref:
            if parent_id:
                # register relationship
                self.parent_map[obj_ref] = parent_id
                self.hierarchy_map.setdefault(parent_id, []).append(obj_ref)
            else:
                # top-level
                self.parent_map.setdefault(obj_ref, None)
            # note: continue walking nested children
            nested = sh_elem.find("reqif:CHILDREN", self.ns) or find_first_child_local(sh_elem, "CHILDREN")
            if nested is not None:
                for child_sh in list(iter_elements_by_local_name(nested, "SPEC-HIERARCHY")):
                    self._walk_spec_hierarchy(child_sh, parent_id=obj_ref)
            else:
                for child_sh in list(iter_elements_by_local_name(sh_elem, "SPEC-HIERARCHY")):
                    if child_sh is not sh_elem:
                        self._walk_spec_hierarchy(child_sh, parent_id=obj_ref)

    # ----------------------------------------------------------
    # Parse relations / traceability (SPEC-RELATIONS or SPEC-RELATION)
    # ----------------------------------------------------------
    def _parse_relations(self, content_root):
        # find SPEC-RELATION elements anywhere (namespace-agnostic)
        rels = content_root.findall(".//reqif:SPEC-RELATION", self.ns)
        if not rels:
            rels = list(iter_elements_by_local_name(content_root, "SPEC-RELATION"))

        for rel in rels:
            # Try several possible encodings for source/target
            source = None
            target = None
            rtype = rel.get("TYPE") or None

            # nested SOURCE/TARGET elements
            src = rel.find("reqif:SOURCE", self.ns) or find_first_child_local(rel, "SOURCE")
            tgt = rel.find("reqif:TARGET", self.ns) or find_first_child_local(rel, "TARGET")
            if src is not None and (src.text or "").strip():
                source = src.text.strip()
            if tgt is not None and (tgt.text or "").strip():
                target = tgt.text.strip()

            # SPEC-OBJECT-REF inside source/target
            if src is not None:
                so_ref = src.find("reqif:SPEC-OBJECT-REF", self.ns) or find_first_child_local(src, "SPEC-OBJECT-REF")
                if so_ref is not None and (so_ref.text or "").strip():
                    source = so_ref.text.strip()
            if tgt is not None:
                to_ref = tgt.find("reqif:SPEC-OBJECT-REF", self.ns) or find_first_child_local(tgt, "SPEC-OBJECT-REF")
                if to_ref is not None and (to_ref.text or "").strip():
                    target = to_ref.text.strip()

            # attributes on the relation
            if not source:
                source = rel.get("SOURCE")
            if not target:
                target = rel.get("TARGET")

            # fallback scan children for any SOURCE-like or TARGET-like
            if not source or not target:
                for child in list(rel):
                    if child is None:
                        continue
                    ctag = local_tag(child).upper()
                    if "SOURCE" in ctag and (child.text or "").strip() and not source:
                        source = child.text.strip()
                    if "TARGET" in ctag and (child.text or "").strip() and not target:
                        target = child.text.strip()
                    if local_tag(child).upper().endswith("SPEC-OBJECT-REF") and (child.text or "").strip():
                        if not source:
                            source = child.text.strip()
                        elif not target:
                            target = child.text.strip()

            if source or target:
                relation = {"source": source, "target": target, "type": rtype}
                self.relations.append(relation)

    # ----------------------------------------------------------
    # Public helper: pretty print requirements (debug)
    # ----------------------------------------------------------
    def pretty_print_requirements(self, requirements: List[ReqIFRequirement]):
        print("\n==================== RESULTS ====================")
        for req in requirements:
            print(f"[{req.id}] {req.title or '(Untitled)'}")
            # Print all attributes to confirm the fix
            for attr_name, attr_value in req.attributes.items():
                # avoid printing internal helpers too verbosely
                if attr_name.startswith("__"):
                    print(f"   {attr_name}: {attr_value}")
                else:
                    print(f"   {attr_name}: {attr_value}")
            print(f"  Description: {req.description or '(No description found)'}\n")


if __name__ == "__main__":
    parser = ReqIFParser("sample.reqif", extract_attachments=False)
    requirements = parser.parse()
    parser.pretty_print_requirements(requirements)







