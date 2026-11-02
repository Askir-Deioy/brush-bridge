"""Explicit product label sharing the CSP SUT codec, with separate report targets."""
from ..core.plugin import FormatCodec, codec, register_codec


@register_codec
class UdongmanCodec(FormatCodec):
    id = "udongman"
    name = "优动漫 PAINT 笔刷 (.sut)"
    extensions = (".sut",)
    can_write = True

    def sniff(self, path):
        return None  # SUT bytes alone cannot reliably identify the originating product.

    def read(self, path):
        return codec("csp").read(path)

    def write(self, brushes, out_path, **options):
        return codec("csp").write(brushes, out_path, **options)
