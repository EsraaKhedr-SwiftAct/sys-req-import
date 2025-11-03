#!/usr/bin/env python3
"""
Full-featured ReqIF parser supporting EA, Polarion, DOORS, and Jama exports.
Handles XHTML, plain text, ENUMs, optional attributes, and nested hierarchy.
"""

import xml.etree.ElementTree as ET
from collections import defaultdict

REQIF_NS = {
    'reqif': 'http://www.omg.org/spec/ReqIF/20110401/reqif.xsd',
    'xhtml': 'http://www.w3.org/1999/xhtml'
}

class ReqIFRequirement:
    def __init__(self, identifier):
        self.identifier = identifier
        self.title = None
        self.description = None
        self.status = None
        self.priority = None
        self.binding = None
        self.verification = None
        self.children = []

    def __repr__(self):
        return f"<ReqIFRequirement id={self.identifier} title={self.title}>"

class ReqIFParser:
    def __init__(self, filename):
        self.filename = filename
        self.requirements = {}
        self.roots = []

    def parse(self):
        tree = ET.parse(self.filename)
        root = tree.getroot()
        content = root.find('reqif:CORE-CONTENT/reqif:REQ-IF-CONTENT', REQIF_NS)

        # Parse all SPEC-OBJECTS
        for obj in content.findall('reqif:SPEC-OBJECTS/reqif:SPEC-OBJECT', REQIF_NS):
            req = self._parse_spec_object(obj)
            self.requirements[req.identifier] = req

        # Build hierarchy
        hierarchy_root = content.find('reqif:SPEC-HIERARCHY/reqif:SPEC-HIERARCHY-ROOT', REQIF_NS)
        if hierarchy_root is not None:
            self._parse_hierarchy(hierarchy_root, None)

    def _parse_spec_object(self, obj):
        identifier = self._get_attribute(obj, 'ID_DEF') or obj.get('IDENTIFIER')
        req = ReqIFRequirement(identifier)
        req.title = self._get_attribute(obj, 'TITLE_DEF')
        req.description = self._get_attribute(obj, 'DESC_DEF', allow_xhtml=True)
        req.status = self._get_attribute(obj, 'STATUS_DEF')
        req.priority = self._get_attribute(obj, 'PRIORITY_DEF')
        req.binding = self._get_attribute(obj, 'BINDING_DEF')
        req.verification = self._get_attribute(obj, 'VERIFICATION_DEF')
        return req

    def _get_attribute(self, obj, attr_ref, allow_xhtml=False):
        # Search ATTRIBUTE-VALUE-STRING
        for attr in obj.findall('reqif:VALUES/*', REQIF_NS):
            definition = attr.find('reqif:DEFINITION/*', REQIF_NS)
            if definition is not None and definition.text == attr_ref:
                if attr.tag.endswith('ATTRIBUTE-VALUE-STRING'):
                    val = attr.find('reqif:THE-VALUE', REQIF_NS)
                    return val.text if val is not None else None
                elif attr.tag.endswith('ATTRIBUTE-VALUE-XHTML') and allow_xhtml:
                    val = attr.find('reqif:THE-VALUE', REQIF_NS)
                    if val is not None:
                        div = val.find('xhtml:div', REQIF_NS)
                        if div is not None:
                            return ''.join(div.itertext()).strip()
                        return val.text.strip() if val.text else None
                elif attr.tag.endswith('ATTRIBUTE-VALUE-ENUMERATION'):
                    # EA may store ENUM as raw text
                    values = attr.findall('reqif:VALUES/reqif:ENUM-VALUE-REF', REQIF_NS)
                    if values:
                        return ','.join(v.text for v in values if v.text)
                    # Fallback for EA raw text
                    val = attr.find('reqif:THE-VALUE', REQIF_NS)
                    if val is not None:
                        return val.text
        return None

    def _parse_hierarchy(self, node, parent_req):
        for child_ref in node.findall('reqif:CHILD-OBJECT-REF', REQIF_NS):
            child_id = child_ref.text
            req = self.requirements.get(child_id)
            if req:
                if parent_req:
                    parent_req.children.append(req)
                else:
                    self.roots.append(req)

        # Recursively parse nested SPEC-HIERARCHY-ROOTs
        for sub_root in node.findall('reqif:SPEC-HIERARCHY-ROOT', REQIF_NS):
            self._parse_hierarchy(sub_root, parent_req)

    def get_all_requirements(self):
        return list(self.requirements.values())

    def get_roots(self):
        return self.roots

# Usage example
if __name__ == "__main__":
    parser = ReqIFParser("sample.reqif")
    parser.parse()
    for r in parser.get_all_requirements():
        print(f"{r.identifier}: {r.title} | {r.status} | {r.description[:50] if r.description else 'No description'}")





