"""
Markdown to Google Docs Converter
Parses markdown and converts it to Google Docs format using the Docs API
"""
import re
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode


class MarkdownToDocsConverter:
    """
    Convert markdown content to Google Docs API requests
    Uses reverse insertion strategy to maintain correct indices
    """

    def __init__(self, markdown_text):
        """
        Initialize converter with markdown text

        Args:
            markdown_text: Raw markdown content to convert
        """
        self.markdown = markdown_text
        self.md_parser = MarkdownIt()
        self.tokens = self.md_parser.parse(markdown_text)
        self.requests = []
        self.current_index = 1  # Google Docs content starts at index 1

    def convert(self):
        """
        Convert markdown to a list of Google Docs API requests

        Returns:
            list: List of request dictionaries for batchUpdate API
        """
        # First pass: collect all content and calculate final length
        content_parts = self._extract_content_parts(self.tokens)

        # Build all text first
        full_text = self._build_full_text(content_parts)

        # Create requests in reverse order for stable indices
        requests = []

        # Insert all text at once
        requests.append({
            'insertText': {
                'location': {'index': 1},
                'text': full_text
            }
        })

        # Apply formatting in reverse order
        current_pos = 1
        for part in content_parts:
            start_index = current_pos
            end_index = start_index + len(part['text'])

            # Apply paragraph style (headings)
            if part.get('heading_level'):
                requests.append({
                    'updateParagraphStyle': {
                        'range': {'startIndex': start_index, 'endIndex': end_index},
                        'paragraphStyle': {
                            'namedStyleType': f"HEADING_{part['heading_level']}"
                        },
                        'fields': 'namedStyleType'
                    }
                })

            # Apply text formatting (bold, italic, code, links)
            if part.get('inline_formats'):
                for fmt in part['inline_formats']:
                    text_style = {}
                    fields = []

                    if fmt.get('bold'):
                        text_style['bold'] = True
                        fields.append('bold')

                    if fmt.get('italic'):
                        text_style['italic'] = True
                        fields.append('italic')

                    if fmt.get('code'):
                        text_style['weightedFontFamily'] = {'fontFamily': 'Courier New'}
                        text_style['fontSize'] = {'magnitude': 10, 'unit': 'PT'}
                        fields.extend(['weightedFontFamily', 'fontSize'])

                    if fmt.get('link'):
                        text_style['link'] = {'url': fmt['link']}
                        fields.append('link')

                    if text_style:
                        fmt_start = start_index + fmt['start']
                        fmt_end = start_index + fmt['end']
                        requests.append({
                            'updateTextStyle': {
                                'range': {'startIndex': fmt_start, 'endIndex': fmt_end},
                                'textStyle': text_style,
                                'fields': ','.join(fields)
                            }
                        })

            # Apply list formatting
            if part.get('list_type'):
                bullet_preset = 'NUMBERED_DECIMAL_ALPHA_ROMAN' if part['list_type'] == 'ordered' else 'BULLET_DISC_CIRCLE_SQUARE'
                requests.append({
                    'createParagraphBullets': {
                        'range': {'startIndex': start_index, 'endIndex': end_index},
                        'bulletPreset': bullet_preset
                    }
                })

            current_pos = end_index

        return requests

    def _extract_content_parts(self, tokens, level=0):
        """
        Extract content parts from markdown tokens

        Returns:
            list: List of content part dictionaries
        """
        parts = []
        i = 0

        while i < len(tokens):
            token = tokens[i]

            if token.type == 'heading_open':
                # Extract heading level
                level = int(token.tag[1])  # h1 -> 1, h2 -> 2, etc.
                inline_token = tokens[i + 1]
                text = self._extract_inline_text(inline_token)
                parts.append({
                    'text': text + '\n',
                    'heading_level': level,
                    'inline_formats': self._extract_inline_formats(inline_token)
                })
                i += 3  # Skip heading_open, inline, heading_close

            elif token.type == 'paragraph_open':
                inline_token = tokens[i + 1]
                text = self._extract_inline_text(inline_token)
                parts.append({
                    'text': text + '\n',
                    'inline_formats': self._extract_inline_formats(inline_token)
                })
                i += 3  # Skip paragraph_open, inline, paragraph_close

            elif token.type == 'bullet_list_open' or token.type == 'ordered_list_open':
                list_type = 'ordered' if token.type == 'ordered_list_open' else 'bullet'
                list_items = self._extract_list_items(tokens, i)
                for item_text, item_formats in list_items['items']:
                    parts.append({
                        'text': item_text + '\n',
                        'list_type': list_type,
                        'inline_formats': item_formats
                    })
                i = list_items['end_index']

            elif token.type == 'code_block' or token.type == 'fence':
                code_text = token.content
                parts.append({
                    'text': code_text + '\n',
                    'inline_formats': [{
                        'start': 0,
                        'end': len(code_text),
                        'code': True
                    }]
                })
                i += 1

            elif token.type == 'hr':
                parts.append({'text': '─' * 50 + '\n'})
                i += 1

            elif token.type == 'blockquote_open':
                # Extract blockquote content
                quote_content = self._extract_blockquote(tokens, i)
                parts.append({
                    'text': '  ' + quote_content + '\n',  # Indent with spaces
                    'inline_formats': []
                })
                i = self._find_matching_close(tokens, i, 'blockquote_close')

            else:
                i += 1

        return parts

    def _extract_inline_text(self, inline_token):
        """Extract plain text from inline token"""
        if not inline_token or not inline_token.children:
            return ''

        text = ''
        for child in inline_token.children:
            if child.type == 'text':
                text += child.content
            elif child.type == 'code_inline':
                text += child.content
            elif child.type in ('strong_open', 'em_open', 'link_open'):
                continue
            elif child.type in ('strong_close', 'em_close', 'link_close'):
                continue

        return text

    def _extract_inline_formats(self, inline_token):
        """Extract formatting information from inline token"""
        if not inline_token or not inline_token.children:
            return []

        formats = []
        pos = 0
        bold = False
        italic = False
        link_url = None

        for child in inline_token.children:
            if child.type == 'strong_open':
                bold = True
            elif child.type == 'strong_close':
                bold = False
            elif child.type == 'em_open':
                italic = True
            elif child.type == 'em_close':
                italic = False
            elif child.type == 'link_open':
                link_url = child.attrGet('href')
            elif child.type == 'link_close':
                link_url = None
            elif child.type == 'text':
                length = len(child.content)
                if bold or italic or link_url:
                    fmt = {'start': pos, 'end': pos + length}
                    if bold:
                        fmt['bold'] = True
                    if italic:
                        fmt['italic'] = True
                    if link_url:
                        fmt['link'] = link_url
                    formats.append(fmt)
                pos += length
            elif child.type == 'code_inline':
                length = len(child.content)
                formats.append({
                    'start': pos,
                    'end': pos + length,
                    'code': True
                })
                pos += length

        return formats

    def _extract_list_items(self, tokens, start_index):
        """Extract all items from a list"""
        items = []
        i = start_index + 1  # Skip list_open

        while i < len(tokens) and tokens[i].type != 'bullet_list_close' and tokens[i].type != 'ordered_list_close':
            if tokens[i].type == 'list_item_open':
                # Next token should be paragraph or inline
                i += 1
                if i < len(tokens):
                    if tokens[i].type == 'paragraph_open':
                        inline_token = tokens[i + 1]
                        text = self._extract_inline_text(inline_token)
                        formats = self._extract_inline_formats(inline_token)
                        items.append((text, formats))
                        i += 3  # Skip paragraph_open, inline, paragraph_close
                    elif tokens[i].type == 'inline':
                        text = self._extract_inline_text(tokens[i])
                        formats = self._extract_inline_formats(tokens[i])
                        items.append((text, formats))
                        i += 1
                i += 1  # Skip list_item_close
            else:
                i += 1

        return {'items': items, 'end_index': i + 1}

    def _extract_blockquote(self, tokens, start_index):
        """Extract text from blockquote"""
        i = start_index + 1
        text = ''

        while i < len(tokens) and tokens[i].type != 'blockquote_close':
            if tokens[i].type == 'paragraph_open':
                inline_token = tokens[i + 1]
                text += self._extract_inline_text(inline_token)
                i += 3
            else:
                i += 1

        return text

    def _find_matching_close(self, tokens, start_index, close_type):
        """Find the matching close token"""
        i = start_index + 1
        while i < len(tokens):
            if tokens[i].type == close_type:
                return i + 1
            i += 1
        return i

    def _build_full_text(self, content_parts):
        """Build the complete text from all content parts"""
        return ''.join(part['text'] for part in content_parts)


def convert_markdown_to_doc_requests(markdown_text):
    """
    Convert markdown text to Google Docs API batch update requests

    Args:
        markdown_text: Raw markdown content

    Returns:
        list: List of requests for the batchUpdate API
    """
    converter = MarkdownToDocsConverter(markdown_text)
    return converter.convert()
