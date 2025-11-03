"""
Portable ReqIF importer using StrictDoc's approach.
Compatible with EA, DOORS, Polarion, Jama, PTC, and any ReqIF-compliant tool.
Extracts:
- Requirement ID
- Title
- Description
- All other attributes (Priority, Status, Binding Force, etc.)
"""

from reqif import ReqIFParser as StrictReqIFParser

class ReqIFImporter:
    """
    Parses a ReqIF file into a simplified list of requirements:
        [{id, title, description, attributes...}, ...]
    """
    def __init__(self, file_path):
        self.file_path = file_path

    def parse(self):
        # Load ReqIF file using StrictDoc's ReqIFParser
        reqif = StrictReqIFParser().load(self.file_path)

        requirements = []

        for spec_object in reqif.spec_objects:
            req_dict = {}

            # 1️⃣ Requirement ID
            req_dict['id'] = spec_object.identifier or "UNKNOWN_ID"

            # 2️⃣ Title
            # Prefer LONG-NAME, otherwise pick a STRING/XHTML attribute with 'title' in name
            title = spec_object.long_name
            if not title:
                for attr in spec_object.attributes:
                    if 'title' in (attr.definition.long_name or '').lower():
                        title = attr.value or ""
                        break
            req_dict['title'] = title or "Untitled Requirement"

            # 3️⃣ Description
            # Aggregate all STRING/XHTML/other textual attributes except the title
            description_lines = []
            for attr in spec_object.attributes:
                name_lower = (attr.definition.long_name or "").lower()
                if 'title' in name_lower:
                    continue
                if attr.value:
                    description_lines.append(str(attr.value))
            req_dict['description'] = "\n".join(description_lines) or "(No description found)"

            # 4️⃣ Other attributes
            for attr in spec_object.attributes:
                key = attr.definition.long_name or attr.definition.identifier
                if key and key.lower() not in ['title', 'description']:
                    val = attr.value
                    # Handle ENUM values if present
                    if getattr(attr.definition, 'values', None) and val in attr.definition.values:
                        val = attr.definition.values[val]
                    if val:
                        req_dict[key] = val

            requirements.append(req_dict)

        return requirements

