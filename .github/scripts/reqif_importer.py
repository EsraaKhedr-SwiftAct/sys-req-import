"""
Official ReqIF importer using the 'reqif' library.
Extracts:
- Requirement ID
- Title
- Description
- All other attributes
Compatible with any ReqIF-compliant tool (Polarion, DOORS, EA, Jama, etc.)
"""

from reqif import ReqIFParser as OfficialReqIFParser

class ReqIFImporter:
    def __init__(self, file_path):
        self.file_path = file_path

    def parse(self):
        reqif = OfficialReqIFParser().load(self.file_path)
        requirements = []

        for spec_object in reqif.spec_objects:
            req_dict = {}

            # Requirement ID
            req_dict['id'] = spec_object.identifier or "UNKNOWN_ID"

            # Title (long_name first, fallback to attribute containing 'title')
            title = spec_object.long_name
            if not title:
                for attr in spec_object.attributes:
                    if 'title' in (attr.definition.long_name or '').lower():
                        title = attr.value
                        break
            req_dict['title'] = title or "Untitled Requirement"

            # Description (aggregate textual attributes except title)
            description_lines = []
            for attr in spec_object.attributes:
                name_lower = (attr.definition.long_name or "").lower()
                if 'title' in name_lower:
                    continue
                if attr.value:
                    description_lines.append(str(attr.value))
            req_dict['description'] = "\n".join(description_lines) or "(No description found)"

            # Other attributes
            for attr in spec_object.attributes:
                key = attr.definition.long_name or attr.definition.identifier
                if key and key.lower() not in ['title', 'description']:
                    val = attr.value
                    if getattr(attr.definition, 'values', None) and val in attr.definition.values:
                        val = attr.definition.values[val]
                    if val:
                        req_dict[key] = val

            requirements.append(req_dict)

        return requirements


