"""Keep pytest out of this directory.

Scripts here run inside Blender (``blender --background --python <script>``)
and import ``bpy``, which does not exist in a plain Python interpreter. Without
this, a bare ``pytest tests`` aborts during collection.
"""

collect_ignore_glob = ["*.py"]
