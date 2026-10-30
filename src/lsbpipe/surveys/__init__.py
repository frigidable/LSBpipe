"""Legacy Imaging Surveys discovery, manifests and verified downloads."""
from .legacy import SkyField, BrickHit, Product, discover_bricks, build_manifest
from .transfer import probe_product, download_manifest

__all__ = ['SkyField', 'BrickHit', 'Product', 'discover_bricks',
           'build_manifest', 'probe_product', 'download_manifest']
