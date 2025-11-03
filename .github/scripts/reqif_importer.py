#!/usr/bin/env python3
"""
reqif_importer.py

Universal ReqIF parser (pure Python, no external ReqIF library required).
Fully compatible with tools like DOORS, Polarion, Jama, EA, PTC, etc.
Mimics 'reqif' library output.

Usage:
    from reqif_importer import ReqIFImporter

    importer = ReqIFImporter("sample.reqif")
    reqs = importer.parse()  # List of ReqIFRequirement
"""

import xml.etree.ElementTree as ET
from collections import defaultdict

class ReqIFRequirement:
    def __init__(self, identifier, title, description, attributes=None, parent_id=None):
        self.identifier = identifier
        self.title = title
        self.description = description
        self.attributes = attributes or {}
        self.parent_id = parent_id

    def __repr__(self):
        return f"<ReqIFRequirement id={self.identifier} title={self.title}>"

class ReqIFBundle:
    def __init__(self):
        self.requirements = []

class ReqIFImporter:
    def __init__(self, file_path):
        self.file_path = file_path

    def parse(self):
        tree = ET.parse(self.file_path)
        root = tree.getroot()

        # Namespace handling
        ns = self._get_namespaces(root)

        # Build attribute definitions mapping
        attr_defs = self._parse_attribute_definitions(root, ns)

        # Build requirements dictionary
        req_dict = self._parse_spec_objects(root, ns, attr_defs)

        # Apply hierarchy relationships
        self._apply_spec_hierarchy(root, ns, req_dict)

        # Flatten to list
        bundle = ReqIFBundle()
        bundle.requirements = list(req_dict.values())
        return bundle.requirements

    def _get_namespaces(self, root):
        ns = {}
        for k, v in root.attrib.items():
            if k.startswith("xmlns:"):
                ns[k.split(":")[1]] = v
        ns["reqif"] = root.tag.split("}")[0].strip("{")
        return ns

    def _parse_attribute_definitions(self, root, ns):
        attr_defs = {}
        for ad in root.findall(".//reqif:ATTRIBUTE-DEFINITIONS/reqif:*", ns):
            attr_id = ad.get("IDENTIFIER")
            attr_defs[attr_id] = ad.tag.split("}")[-1]
        return attr_defs

    def _parse_spec_objects(self, root, ns, attr_defs):
        req_dict = {}
        for obj in root.findall(".//reqif:SPEC-OBJECT", ns):
            identifier = obj.findtext("reqif:IDENTIFIER", default=None, namespaces=ns)
            title = None
            description = None
            attributes = {}
            for val in obj.findall("reqif:VALUES/*", ns):
                attr_id = val.get("DEFINITION")
                attr_value = val.findtext("reqif:THE-VALUE", default=None, namespaces=ns)
                if attr_id in ["AD_TITLE", "AD_NAME", "TITLE"]:
                    title = attr_value
                elif attr_id in ["AD_DESC", "DESC", "DESCRIPTION"]:
                    description = attr_value
                else:
                    attributes[attr_id] = attr_value
            req_dict[identifier] = ReqIFRequirement(
                identifier=identifier,
                title=title or "",
                description=description or "",
                attributes=attributes,
            )
        return req_dict

    def _apply_spec_hierarchy(self, root, ns, req_dict):
        for sh in root.findall(".//reqif:SPEC-HIERARCHY", ns):
            parent_obj_id = sh.findtext("reqif:OBJECT", default=None, namespaces=ns)
            children = sh.findall("reqif:CHILDREN/reqif:SPEC-HIERARCHY", ns)
            for child in children:
                child_obj_id = child.findtext("reqif:OBJECT", default=None, namespaces=ns)
                if child_obj_id in req_dict:
                    req_dict[child_obj_id].parent_id = parent_obj_id
                # recursively handle deeper hierarchy
                self._apply_spec_hierarchy_recursive(child, ns, req_dict, parent_obj_id)

    def _apply_spec_hierarchy_recursive(self, node, ns, req_dict, parent_id):
        children = node.findall("reqif:CHILDREN/reqif:SPEC-HIERARCHY", ns)
        for child in children:
            child_obj_id = child.findtext("reqif:OBJECT", default=None, namespaces=ns)
            if child_obj_id in req_dict:
                req_dict[child_obj_id].parent_id = parent_id
            self._apply_spec_hierarchy_recursive(child, ns, req_dict, child_obj_id)




