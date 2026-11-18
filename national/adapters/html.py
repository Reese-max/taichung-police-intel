"""Small stdlib HTML tree for the two WebForms list adapters."""
from html.parser import HTMLParser


class Node:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def find(self, tag=None, **attrs):
        result = []
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and all(child.attrs.get(k) == v for k, v in attrs.items()):
                    result.append(child)
                result.extend(child.find(tag, **attrs))
        return result

    def text(self):
        return " ".join(" ".join(c.text() if isinstance(c, Node) else c
                                 for c in self.children).split())


class Document(HTMLParser):
    VOID = set("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def find(self, tag=None, **attrs):
        return self.root.find(tag, **attrs)

    def one(self, tag=None, **attrs):
        nodes = self.find(tag, **attrs)
        if len(nodes) != 1:
            raise ValueError(f"expected one HTML element: {tag} {attrs}")
        return nodes[0]

    def options(self, name):
        selects = self.find("select", name=name)
        return [(n.attrs.get("value", ""), n.text()) for s in selects for n in s.find("option")]

    def form(self):
        result = {}
        for node in self.one("form", id="form1").find("input"):
            attrs = node.attrs
            kind = attrs.get("type", "text").lower()
            if (not attrs.get("name") or "disabled" in attrs or kind in {"submit", "button", "image", "file"}
                    or kind in {"checkbox", "radio"} and "checked" not in attrs):
                continue
            result[attrs["name"]] = attrs.get("value", "on" if kind in {"checkbox", "radio"} else "")
        for node in self.one("form", id="form1").find("select"):
            options = node.find("option")
            selected = [o for o in options if "selected" in o.attrs] or options[:1]
            if node.attrs.get("name") and selected:
                result[node.attrs["name"]] = selected[0].attrs.get("value", "")
        return result
