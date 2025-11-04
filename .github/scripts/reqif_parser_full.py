#!/usr/bin/env python3
"""
reqif_parser_full.py (Debug-enabled)

Includes verbose debugging to trace title/description detection problems,
and now correctly extracts Enumeration, Integer, and Boolean attribute values.
"""

import xml.etree.ElementTree as ET


class ReqIFRequirement:
    def __init__(self, identifier, title="", description="", attributes=None):
        self.id = identifier
        self.title = title
        self.description = description
        self.attributes = attributes or {}


class ReqIFParser:
    REQIF_NS = {
        "reqif": "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd",
        "xhtml": "http://www.w3.org/1999/xhtml",
    }

    def __init__(self, filename):
        print(f"🔍 Loading ReqIF file: {filename}")
        self.filename = filename
        self.tree = ET.parse(filename)
        self.root = self.tree.getroot()
        self.ns = self._detect_ns()
        print(f"✅ Detected namespace map: {self.ns}")
        
        # --- NEW/UPDATED INITIALIZATION ---
        self.def_map = self._build_definition_map() # Maps ATTRIBUTE_DEF ID to Long Name (e.g., ID_DEF -> ID)
        self.enum_map = self._build_enum_map()     # Maps ENUM_ID to Long Name (e.g., ENUM_HIGH -> High)
        
        print(f"✅ Definition map built ({len(self.def_map)} items):")
        for k, v in self.def_map.items():
            print(f"   - {k} → {v}")
        print(f"✅ Enumeration map built ({len(self.enum_map)} items).")
        # ----------------------------------

    # ----------------------------------------------------------
    # Detects if file uses default namespace or prefixes
    # ----------------------------------------------------------
    def _detect_ns(self):
        tag = self.root.tag
        if tag.startswith("{"):
            uri = tag.split("}")[0].strip("{}")
            return {"reqif": uri, "xhtml": "http://www.w3.org/1999/xhtml"}
        return self.REQIF_NS

    def _build_definition_map(self):
        mapping = {}
        for def_type in [
            "ATTRIBUTE-DEFINITION-STRING",
            "ATTRIBUTE-DEFINITION-XHTML",
            "ATTRIBUTE-DEFINITION-ENUMERATION",
            "ATTRIBUTE-DEFINITION-INTEGER",
            "ATTRIBUTE-DEFINITION-BOOLEAN",
            "ATTRIBUTE-DEFINITION-DATE",
        ]:
            found = self.root.findall(f".//reqif:{def_type}", self.ns)
            if found:
                print(f"📦 Found {len(found)} {def_type} elements")
            for attr_def in found:
                def_id = attr_def.get("IDENTIFIER")
                long_name = attr_def.get("LONG-NAME", "")
                if def_id and long_name:
                    mapping[def_id] = long_name
        return mapping

    # --- NEW METHOD: Maps Enumeration IDs to their Long Names ---
    def _build_enum_map(self):
        """Builds a map of {ENUM_ID: LONG-NAME} for all enumerations (e.g., ENUM_HIGH -> High)."""
        enum_mapping = {}
        # Find all DATATYPE-DEFINITION-ENUMERATION elements
        for dt_enum in self.root.findall(".//reqif:DATATYPE-DEFINITION-ENUMERATION", self.ns):
            # Find all SPEC-ENUMERATION-VALUE within
            for enum_value in dt_enum.findall(".//reqif:SPEC-ENUMERATION-VALUE", self.ns):
                enum_id = enum_value.get("IDENTIFIER")
                long_name = enum_value.get("LONG-NAME")
                if enum_id and long_name:
                    enum_mapping[enum_id] = long_name
        return enum_mapping
    # ------------------------------------------------------------


    def parse(self):
        reqs = []
        # Finding SPEC-OBJECTs directly under REQ-IF-CONTENT is usually more robust than searching the whole root
        content = self.root.find("reqif:CORE-CONTENT/reqif:REQ-IF-CONTENT", self.ns)
        if content is None:
            print("❌ Error: Could not find REQ-IF-CONTENT.")
            return []
            
        spec_objects = content.findall(".//reqif:SPEC-OBJECT", self.ns)
        print(f"📄 Found {len(spec_objects)} SPEC-OBJECT elements")

        for spec_obj in spec_objects:
            identifier = spec_obj.get("IDENTIFIER", "UNKNOWN")
            print(f"\n🔹 Parsing SPEC-OBJECT: {identifier}")
            
            # --- UPDATED: Pass the spec_obj directly to collection ---
            attributes = self._collect_attributes(spec_obj)
            # --------------------------------------------------------
            
            print(f"   Attributes found: {list(attributes.keys())}")

            title = self._find_flexible(attributes, ["Title", "Name", "Req Title", "Requirement"])
            description = self._find_flexible(attributes, ["Description", "Desc", "Text", "Body", "Content"])
            print(f"   → Detected Title: {title!r}")
            print(f"   → Detected Description: {description!r}")

            reqs.append(ReqIFRequirement(identifier, title, description, attributes))
        return reqs

    # --- UPDATED METHOD: Simplified Attribute Collection ---
    def _collect_attributes(self, spec_obj):
        attrs = {}
        # Find all ATTRIBUTE-VALUE-* tags directly under VALUES
        all_attrs = spec_obj.findall("reqif:VALUES/*", self.ns) 

        print(f"   → Found {len(all_attrs)} ATTRIBUTE-VALUE elements")

        for attr in all_attrs:
            
            # 1. Find the Definition ID (e.g., 'PRIORITY_DEF')
            ref_elem = attr.find("reqif:DEFINITION/*", self.ns)
            if ref_elem is None or ref_elem.text is None:
                continue 

            ref_id = ref_elem.text.strip()
            
            # 2. Get the Attribute's Long Name (e.g., 'Priority') from the global map
            attr_name = self.def_map.get(ref_id) 

            if not attr_name:
                continue

            # 3. Extract the raw value using the fixed _extract_value
            value = self._extract_value(attr)
            
            print(f"      DEF={attr_name!r} → VALUE={value!r}") 
            attrs[attr_name] = value

        return attrs
    # -------------------------------------------------------


    def _resolve_definition_name(self, attr):
        # NOTE: This method is now effectively replaced by the logic in _collect_attributes.
        # However, keeping it for compatibility if other parts of your project use it.
        # The logic below is less reliable than the new _collect_attributes structure.
        for def_type in [
            "ATTRIBUTE-DEFINITION-STRING-REF",
            "ATTRIBUTE-DEFINITION-XHTML-REF",
            "ATTRIBUTE-DEFINITION-ENUMERATION-REF",
            "ATTRIBUTE-DEFINITION-INTEGER-REF",
            "ATTRIBUTE-DEFINITION-BOOLEAN-REF",
            "ATTRIBUTE-DEFINITION-DATE-REF",
        ]:
            ref_elem = attr.find(f"reqif:DEFINITION/reqif:{def_type}", self.ns)
            if ref_elem is not None and ref_elem.text:
                ref_id = ref_elem.text.strip()
                if ref_id in self.def_map:
                    return self.def_map[ref_id]
                for known_id, long_name in self.def_map.items():
                    if ref_id.lower() in known_id.lower() or known_id.lower() in ref_id.lower():
                        return long_name
        return None

    # --- UPDATED METHOD: Correctly extracts all value types ---
    def _extract_value(self, attr):
        tag = attr.tag.split("}")[-1]
        
        # Handle STRING
        if tag == "ATTRIBUTE-VALUE-STRING":
            val = attr.find("reqif:THE-VALUE", self.ns)
            return val.text.strip() if val is not None and val.text else ""
            
        # Handle XHTML
        if tag == "ATTRIBUTE-VALUE-XHTML":
            val_elem = attr.find("reqif:THE-VALUE", self.ns)
            if val_elem is not None:
                div_elem = val_elem.find("xhtml:div", self.ns)
                content = div_elem if div_elem is not None else val_elem
                return "".join(content.itertext()).strip()
            return ""

        # Handle INTEGER/BOOLEAN/DATE (FIXED: Uses THE-VALUE directly)
        if tag in ["ATTRIBUTE-VALUE-INTEGER", "ATTRIBUTE-VALUE-BOOLEAN", "ATTRIBUTE-VALUE-DATE"]:
            val = attr.find("reqif:THE-VALUE", self.ns)
            return val.text.strip().lower() if val is not None and val.text else ""
            
        # Handle ENUMERATION (FIXED: Uses enum_map for resolution)
        if tag == "ATTRIBUTE-VALUE-ENUMERATION":
            # Standard: <THE-VALUE><ENUM-REF>ENUM_ID</ENUM-REF></THE-VALUE>
            enum_ref = attr.find("reqif:THE-VALUE/reqif:ENUM-REF", self.ns)
            if enum_ref is not None and enum_ref.text:
                enum_id = enum_ref.text.strip()
                # Resolve the ID (e.g., ENUM_HIGH) to the Long Name (e.g., High)
                return self.enum_map.get(enum_id, f"Unresolved Enum: {enum_id}")
            
            # Fallback: Raw value in <THE-VALUE>
            val = attr.find("reqif:THE-VALUE", self.ns)
            return val.text.strip() if val is not None and val.text else ""

        return ""
    # ----------------------------------------------------------

    def _find_flexible(self, attributes, keys):
        for k in keys:
            for akey, val in attributes.items():
                if k.lower() in akey.lower():
                    return val
        return ""


if __name__ == "__main__":
    parser = ReqIFParser("sample.reqif")
    requirements = parser.parse()
    print("\n==================== RESULTS ====================")
    for req in requirements:
        print(f"[{req.id}] {req.title or '(Untitled)'}")
        # Print all attributes to confirm the fix
        for attr_name, attr_value in req.attributes.items():
            print(f"   {attr_name}: {attr_value}")
        print(f"  Description: {req.description or '(No description found)'}\n")




