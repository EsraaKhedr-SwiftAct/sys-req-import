"""
reqif_importer.py

Lightweight wrapper to parse .reqif files using the official 'reqif' library.
Compatible with EA, DOORS, Polarion, Jama, and PTC ReqIF dialects.
"""

from reqif.parser import ReqIFParser

class ReqIFImporter:
    def __init__(self, file_path):
        self.file_path = file_path

    def parse(self):
        """
        Parses the ReqIF file and returns a list of requirements dictionaries.

        Each requirement dictionary contains:
        - 'id': SpecObject identifier
        - 'title': The title of the requirement
        - 'description': Concatenated attribute values excluding title
        - Additional attributes as key-value pairs
        """
        print(f"📄 Parsing ReqIF file: {self.file_path}")
        reqif_bundle = ReqIFParser.parse(self.file_path)

        requirements = []
        for spec_object in reqif_bundle.core_content.req_if_content.spec_objects:
            req_dict = {}
            req_dict['id'] = spec_object.identifier or "UNKNOWN_ID"

            # Determine the title
            title = spec_object.long_name
            if not title:
                for attr in spec_object.attributes:
                    if attr.definition.long_name and 'title' in attr.definition.long_name.lower():
                        title = attr.value or ""
                        break
            req_dict['title'] = title or "Untitled Requirement"

            # Build description
            description_lines = []
            for attr in spec_object.attributes:
                name_lower = (attr.definition.long_name or "").lower()
                if 'title' in name_lower:
                    continue
                if attr.value:
                    description_lines.append(str(attr.value))
            req_dict['description'] = "\n".join(description_lines) or "(No description found)"

            # Add other attributes
            for attr in spec_object.attributes:
                key = attr.definition.long_name or attr.definition.identifier
                if key and key.lower() not in ['title', 'description']:
                    val = attr.value
                    if getattr(attr.definition, 'values', None) and val in attr.definition.values:
                        val = attr.definition.values[val]
                    if val is not None:
                        req_dict[key] = val

            requirements.append(req_dict)

        return requirements


# Example usage:
if __name__ == "__main__":
    importer = ReqIFImporter("sample.reqif")
    reqs = importer.parse()
    print(f"Parsed {len(reqs)} requirements.")
    for r in reqs:
        print(r["id"], r["title"])



