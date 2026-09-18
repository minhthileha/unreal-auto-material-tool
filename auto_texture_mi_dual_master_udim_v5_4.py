# -*- coding: utf-8 -*-
"""
UE Auto Texture -> Local Folder -> MI -> Mesh Assigner v5.4

Based on v4, with production-naming hardening added.

NEW IN V5.4
-----------
- Expands safe trailing DCC/Maya junk-name cleanup.
- Handles common shader/material leftovers such as:
    lambert#, phong#, blinn#, aiStandardSurface#, standardSurface#,
    surfaceShader#, anisotropic#, rampShader#, layeredShader#,
    shadingGroup, shadingEngine, initialShadingGroup, SG,
    checker#, file#, mat#, material#, shadingpre#, etc.
- These are stripped ONLY when they appear as trailing asset-name noise,
  reducing the chance of damaging legitimate asset names.

NEW IN V5.3
-----------
- Treats trailing _checker / _checker1 / _checker2... as DCC naming noise.
- Example:
    T_cau_thang_checker_BaseColor -> canonical asset: cau_thang
    -> MI_cau_thang -> mesh SM_cau_thang

NEW IN V5.2
-----------
- Verifies that texture parameters are stored as explicit Material Instance overrides.
- If Unreal's normal MaterialEditingLibrary setter does not enable/persist the
  override (observed on Virtual NormalTex), writes the MIC texture override array
  directly and verifies it.

NEW IN V5
---------
1) Canonical asset naming:
   - Strips version suffixes such as _v2, _v003, _ver2, _version_04, _rev3.
   - Strips configurable junk suffixes such as _shadingpre, _lambert1, _mat, _final.
   - Example:
       T_tu_ghe_shadingpre_v2_BaseColor -> canonical asset name: tu_ghe

2) New texture versions update the SAME canonical MI:
   - T_Bottle_BaseColor
   - T_Bottle_v2_BaseColor
   - T_Bottle_final_v3_BaseColor
     all resolve to MI_Bottle (instead of generating separate versioned MIs).

3) When duplicate texture types exist in the same group:
   - Highest explicit version is preferred.
   - Useful when v1/v2/v3 coexist in the project.

4) Safer mesh matching:
   - Exact canonical match first.
   - Optional alias table.
   - Safe fuzzy fallback with threshold + ambiguity protection.
   - Low-confidence matches are only suggested, NOT assigned.

5) Keeps v4 functionality:
   - Regular + UDIM/Virtual master materials.
   - Raw UDIM tile safety.
   - Automatic texture settings.
   - Local texture folders.
   - MI creation/update.
   - Texture parameter assignment.
   - Static Mesh material assignment.

IMPORTANT
---------
Texture TYPE still needs to be identifiable from the texture name:
    BaseColor / Normal / ORM / Emissive (or configured aliases).

A texture named only:
    T_tu_ghe_shadingpre
does not safely tell the tool whether it is BaseColor, Normal, ORM, etc.
The tool will skip it and warn instead of guessing incorrectly.
"""

import re
from difflib import SequenceMatcher
from collections import defaultdict
import unreal


# =========================================================
# CONFIG
# =========================================================

# If textures are selected in Content Browser, only selected textures are processed.
USE_SELECTED_ASSETS_FIRST = True

# If nothing is selected, scan these roots. /Game is flexible but slower.
TEXTURE_SCAN_ROOTS = ["/Game"]


# ---------------------------------------------------------
# MASTER MATERIALS
# ---------------------------------------------------------

MASTER_MATERIAL_REGULAR_CANDIDATES = [
    "/Game/Texture/M_Master_Auto_Stylized",
    "/Game/M_Master_Auto_Stylized",
]

MASTER_MATERIAL_UDIM_CANDIDATES = [
    "/Game/Texture/M_Master_Auto_UDIM",
    "/Game/Texture/M_Master_Auto_Stylized_UDIM",
    "/Game/M_Master_Auto_UDIM",
    "/Game/M_Master_Auto_Stylized_UDIM",
]

# If hard paths fail, search /Game by these asset names.
MASTER_REGULAR_NAMES = ["M_Master_Auto_Stylized"]
MASTER_UDIM_NAMES = ["M_Master_Auto_UDIM", "M_Master_Auto_Stylized_UDIM"]


# ---------------------------------------------------------
# MESH ASSIGNMENT
# ---------------------------------------------------------

ASSIGN_TO_MESH_ASSET = True
MESH_SCAN_ROOTS = ["/Game/Mesh", "/Game"]

# Optional: also update placed level actors that use the matched Static Mesh.
ASSIGN_TO_LEVEL_ACTORS = False

# Existing workflow assigns the same MI to every material slot.
# Set False if you prefer to skip multi-slot meshes.
ASSIGN_ALL_MESH_SLOTS = True


# ---------------------------------------------------------
# FOLDER / ASSET CHANGES
# ---------------------------------------------------------

DRY_RUN = False
MOVE_TEXTURES_INTO_LOCAL_FOLDER = True
CREATE_MI = True
FORCE_EXISTING_MI_PARENT = True
SAVE_ASSETS = True


# ---------------------------------------------------------
# UDIM SAFETY
# ---------------------------------------------------------

# Raw tiles such as BaseColor_1001 / _1002 / _1003 are NOT automatically
# treated as a grouped Unreal Virtual Texture asset.
SKIP_RAW_UDIM_TILE_GROUPS = True


# ---------------------------------------------------------
# DIRTY NAMING / VERSION HANDLING
# ---------------------------------------------------------

# If several versions of the same texture type are found in one canonical group,
# prefer the highest explicit version number.
PREFER_HIGHEST_VERSION = True

# Common junk suffixes produced by DCC/material/export workflows.
# Only trailing tokens are removed, which is safer than deleting words anywhere.
NOISE_SUFFIX_TOKENS = {
    # Maya / DCC material & shader leftovers
    "lambert",
    "phong",
    "blinn",
    "aistandardsurface",
    "standardsurface",
    "surfaceshader",
    "anisotropic",
    "rampshader",
    "layeredshader",
    "shadinggroup",
    "shadingengine",
    "initialshadinggroup",
    "sg",
    "shader",
    "shading",
    "shadingpre",
    "shadingpreview",

    # Utility / texture-node leftovers
    "checker",
    "file",

    # Generic production suffix noise
    "mat",
    "material",
    "preview",
    "pre",
    "test",
    "temp",
    "tmp",
    "final",
    "export",
}

# Additional suffix patterns such as lambert1 / mat02 / shadingpre3.
NOISE_SUFFIX_PATTERNS = [
    # Maya / DCC shader names with optional numeric suffix
    r"lambert\d*",
    r"phong\d*",
    r"blinn\d*",
    r"aistandardsurface\d*",
    r"standardsurface\d*",
    r"surfaceshader\d*",
    r"anisotropic\d*",
    r"rampshader\d*",
    r"layeredshader\d*",
    r"shadinggroup\d*",
    r"shadingengine\d*",
    r"initialshadinggroup\d*",
    r"sg\d*",

    # Utility / texture node leftovers
    r"checker\d*",
    r"file\d*",

    # Generic material / shader suffixes
    r"mat\d*",
    r"material\d*",
    r"shader\d*",
    r"shading\d*",
    r"shadingpre\d*",
    r"shadingpreview\d*",
]

# Manual overrides for especially cursed names.
#
# Keys and values are compared in canonical lowercase form.
# Add project-specific exceptions here when necessary.
#
# Example:
# ASSET_ALIASES = {
#     "tu_ghe_superweird": "tu_ghe",
#     "chairheroexport": "chair_hero",
# }
ASSET_ALIASES = {
}


# ---------------------------------------------------------
# SAFE FUZZY MATCHING
# ---------------------------------------------------------

ENABLE_FUZZY_MESH_MATCH = True

# Automatically assign only when confidence is very high.
FUZZY_AUTO_ASSIGN_THRESHOLD = 0.92

# Scores below auto-assign but above this value are logged as suggestions.
FUZZY_SUGGEST_THRESHOLD = 0.76

# If the best two candidates are too close, do not auto-assign.
FUZZY_AMBIGUITY_MARGIN = 0.05

# Avoid fuzzy matching absurdly short names.
MIN_FUZZY_NAME_LENGTH = 4


# =========================================================
# PARAMETER NAMES IN MASTER MATERIALS
# =========================================================

SUFFIX_MAP = {
    "basecolor": "BaseColorTex",
    "normal": "NormalTex",
    "orm": "ORMTex",
    "emissive": "EmissiveTex",
}

COLOR_TEXTURE_TYPES = ["basecolor", "emissive"]


# =========================================================
# TEXTURE TYPE ALIASES
# =========================================================

TYPE_ALIASES = {
    # Base color
    "basecolor": "basecolor",
    "basecolour": "basecolor",
    "base": "basecolor",
    "albedo": "basecolor",
    "diffuse": "basecolor",
    "color": "basecolor",
    "colour": "basecolor",

    # Normal
    "normal": "normal",
    "normalmap": "normal",
    "nrm": "normal",
    "nor": "normal",
    "norm": "normal",

    # Common Substance / DCC normal suffixes
    "normaldx": "normal",
    "normaldirectx": "normal",
    "directxnormal": "normal",
    "normalgl": "normal",
    "normalopengl": "normal",
    "openglnormal": "normal",

    # ORM / packed masks
    "orm": "orm",
    "aorm": "orm",
    "arm": "orm",
    "occlusionroughnessmetallic": "orm",
    "ambientocclusionroughnessmetallic": "orm",
    "aoroughnessmetallic": "orm",

    # Emissive
    "emissive": "emissive",
    "emission": "emissive",
    "emit": "emissive",
}


# =========================================================
# LOG
# =========================================================

LOG_PREFIX = "[AUTO_TEXTURE_MI_V5_4] "


def log(msg):
    unreal.log(LOG_PREFIX + str(msg))


def warn(msg):
    unreal.log_warning(LOG_PREFIX + str(msg))


def err(msg):
    unreal.log_error(LOG_PREFIX + str(msg))


# =========================================================
# BASIC HELPERS
# =========================================================

def clean_name(name):
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(name))
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def package_path(asset):
    """/Game/Folder/Asset.Asset -> /Game/Folder/Asset"""
    return asset.get_path_name().split(".")[0]


def package_dir(asset_or_path):
    if not isinstance(asset_or_path, str):
        path = package_path(asset_or_path)
    else:
        path = asset_or_path.split(".")[0]

    parts = path.split("/")
    if len(parts) <= 2:
        return "/Game"

    return "/".join(parts[:-1])


def normalize_key(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def alias_key(s):
    """Key format used by ASSET_ALIASES."""
    return clean_name(s).lower()


# =========================================================
# CANONICAL NAMING
# =========================================================

VERSION_TOKEN_RE = re.compile(
    r"^(?:v|ver|version|rev|revision|r)[_-]?(\d+)$",
    re.IGNORECASE
)


def strip_known_asset_prefix(name):
    """
    Remove common Unreal asset prefixes for matching only.

    Examples:
        SM_tu_ghe -> tu_ghe
        T_tu_ghe  -> tu_ghe
        MI_tu_ghe -> tu_ghe
    """
    name = clean_name(name)

    known_prefixes = (
        "StaticMesh_",
        "STATICMESH_",
        "SM_",
        "sm_",
        "MI_",
        "mi_",
        "T_",
        "t_",
    )

    for prefix in known_prefixes:
        if name.startswith(prefix):
            return name[len(prefix):]

    return name


def is_noise_suffix_token(token):
    token_lower = str(token).lower()

    if token_lower in NOISE_SUFFIX_TOKENS:
        return True

    for pattern in NOISE_SUFFIX_PATTERNS:
        if re.fullmatch(pattern, token_lower):
            return True

    return False


def parse_version_token(token):
    match = VERSION_TOKEN_RE.fullmatch(str(token))
    if not match:
        return None

    try:
        return int(match.group(1))
    except Exception:
        return None


def canonicalize_asset_name(name, remove_prefix=True):
    """
    Convert production-weird names into a stable asset identity.

    Examples:
        tu_ghe_shadingpre
            -> tu_ghe

        tu_ghe_final_v2
            -> tu_ghe

        tu_ghe_v2_final
            -> tu_ghe

        SM_tu_ghe
            -> tu_ghe

    Only TRAILING noise/version tokens are removed.
    This avoids aggressively deleting legitimate words in the middle of names.
    """
    cleaned = clean_name(name)

    if remove_prefix:
        cleaned = strip_known_asset_prefix(cleaned)

    parts = [p for p in cleaned.split("_") if p]

    changed = True
    while parts and changed:
        changed = False

        last = parts[-1]

        if parse_version_token(last) is not None:
            parts.pop()
            changed = True
            continue

        if is_noise_suffix_token(last):
            parts.pop()
            changed = True
            continue

    canonical = clean_name("_".join(parts))

    if not canonical:
        canonical = clean_name(cleaned)

    # Manual alias override after automatic cleanup.
    key = alias_key(canonical)
    alias_target = ASSET_ALIASES.get(key)

    if alias_target:
        canonical = clean_name(alias_target)

    return canonical


def canonical_key(name):
    return canonicalize_asset_name(name).lower()


def extract_explicit_version(name):
    """
    Return the highest explicit version token found in the asset identity section.

    Examples:
        tu_ghe_v2                -> 2
        tu_ghe_final_v003        -> 3
        tu_ghe_v2_final          -> 2
        tu_ghe                   -> None
    """
    cleaned = strip_known_asset_prefix(clean_name(name))
    parts = [p for p in cleaned.split("_") if p]

    versions = []

    for token in parts:
        version = parse_version_token(token)
        if version is not None:
            versions.append(version)

    return max(versions) if versions else None


# =========================================================
# TEXTURE DETECTION
# =========================================================

def detect_texture(texture_name):
    """
    Accepts examples such as:
        T_House_BaseColor
        T_House_v2_BaseColor
        T_House_final_v3_Normal
        T_tu_ghe_shadingpre_ORM
        T_House_BaseColor_1001
        House_Normal

    Returns:
        {
            raw_base_name: original detected asset section,
            base_name: canonical stable name,
            canonical_key: lowercase stable name,
            folder_name: T_<canonical name>,
            tex_type: basecolor/normal/orm/emissive,
            version: int or None,
            udim_tile: 1001 or None,
            is_raw_udim_tile: bool
        }
    """
    name = str(texture_name)

    if name.startswith("T_"):
        no_prefix = name[2:]
    else:
        no_prefix = name

    parts = [p for p in no_prefix.split("_") if p]

    if len(parts) < 2:
        warn("Skip texture with insufficient naming information: {}".format(texture_name))
        return None

    # Detect raw UDIM tile at very end.
    udim_tile = None
    if re.fullmatch(r"1\d{3}", parts[-1]):
        udim_tile = parts[-1]
        parts = parts[:-1]

    if len(parts) < 2:
        warn("Skip texture after UDIM parsing; cannot determine type: {}".format(texture_name))
        return None

    # Detect texture type using up to 4 trailing tokens.
    # Prefer longer aliases such as AO_Roughness_Metallic.
    max_suffix = min(4, len(parts) - 1)
    found_type = None
    suffix_len = 0

    for n in range(max_suffix, 0, -1):
        candidate = "_".join(parts[-n:])
        key = normalize_key(candidate)

        if key in TYPE_ALIASES:
            found_type = TYPE_ALIASES[key]
            suffix_len = n
            break

    if not found_type:
        warn(
            "Skip texture; cannot safely infer map type from name: {}. "
            "Expected BaseColor / Normal / ORM / Emissive (or configured alias).".format(
                texture_name
            )
        )
        return None

    base_parts = parts[:-suffix_len]

    if not base_parts:
        return None

    raw_base_name = clean_name("_".join(base_parts))

    if not raw_base_name:
        return None

    canonical_name = canonicalize_asset_name(raw_base_name, remove_prefix=False)

    if not canonical_name:
        canonical_name = raw_base_name

    version = extract_explicit_version(raw_base_name)

    if canonical_name.lower() != raw_base_name.lower():
        log(
            "Canonical name: {} -> {}{}".format(
                raw_base_name,
                canonical_name,
                " | version={}".format(version) if version is not None else ""
            )
        )

    return {
        "raw_base_name": raw_base_name,
        "base_name": canonical_name,
        "canonical_key": canonical_name.lower(),
        "folder_name": "T_" + canonical_name,
        "tex_type": found_type,
        "version": version,
        "udim_tile": udim_tile,
        "is_raw_udim_tile": udim_tile is not None,
    }


# =========================================================
# TEXTURE SETTINGS
# =========================================================

def is_virtual_texture(texture):
    try:
        return bool(texture.get_editor_property("virtual_texture_streaming"))
    except Exception:
        return False


def safe_save(asset):
    if not SAVE_ASSETS or DRY_RUN:
        return

    try:
        unreal.EditorAssetLibrary.save_loaded_asset(asset)
    except Exception as e:
        warn("Save failed for {}: {}".format(asset.get_name(), e))


def setup_texture(texture, tex_type):
    """
    Automatic texture settings.

    Does NOT force virtual_texture_streaming on because raw UDIM tiles
    must not be faked as grouped UDIM Virtual Texture assets.
    """
    if DRY_RUN:
        log("DRY texture settings: {} type={}".format(texture.get_name(), tex_type))
        return

    try:
        if tex_type in COLOR_TEXTURE_TYPES:
            texture.set_editor_property("srgb", True)
            texture.set_editor_property(
                "compression_settings",
                unreal.TextureCompressionSettings.TC_DEFAULT
            )

        elif tex_type == "normal":
            texture.set_editor_property("srgb", False)
            texture.set_editor_property(
                "compression_settings",
                unreal.TextureCompressionSettings.TC_NORMALMAP
            )

            # Substance may export Normal_OpenGL / Normal_DirectX.
            # Unreal expects DirectX-style normals. If the filename explicitly
            # says OpenGL, flip the green channel automatically.
            normal_name_key = normalize_key(texture.get_name())

            is_opengl_normal = (
                normal_name_key.endswith("normalopengl")
                or normal_name_key.endswith("openglnormal")
                or normal_name_key.endswith("normalgl")
            )

            is_directx_normal = (
                normal_name_key.endswith("normaldirectx")
                or normal_name_key.endswith("directxnormal")
                or normal_name_key.endswith("normaldx")
            )

            try:
                if is_opengl_normal:
                    texture.set_editor_property("flip_green_channel", True)
                    log("Normal OpenGL detected -> Flip Green ON: " + texture.get_name())

                elif is_directx_normal:
                    texture.set_editor_property("flip_green_channel", False)
                    log("Normal DirectX detected -> Flip Green OFF: " + texture.get_name())

            except Exception as e:
                warn(
                    "Could not set Flip Green Channel for {}: {}".format(
                        texture.get_name(),
                        e
                    )
                )

        elif tex_type == "orm":
            texture.set_editor_property("srgb", False)
            texture.set_editor_property(
                "compression_settings",
                unreal.TextureCompressionSettings.TC_MASKS
            )

        safe_save(texture)

    except Exception as e:
        warn("Texture setup failed for {}: {}".format(texture.get_name(), e))


# =========================================================
# FOLDER ORGANIZATION
# =========================================================

def move_asset_to_folder(asset, target_folder):
    old_path = package_path(asset)
    new_path = target_folder + "/" + asset.get_name()

    if old_path == new_path:
        return asset

    if not MOVE_TEXTURES_INTO_LOCAL_FOLDER:
        return asset

    if DRY_RUN:
        log("DRY move: {} -> {}".format(old_path, new_path))
        return asset

    if not unreal.EditorAssetLibrary.does_directory_exist(target_folder):
        unreal.EditorAssetLibrary.make_directory(target_folder)
        log("Created folder: " + target_folder)

    if unreal.EditorAssetLibrary.does_asset_exist(new_path):
        warn("Target exists, using existing asset: " + new_path)
        loaded_asset = unreal.EditorAssetLibrary.load_asset(new_path)
        return loaded_asset if loaded_asset else asset

    ok = unreal.EditorAssetLibrary.rename_asset(old_path, new_path)

    if not ok:
        warn("Move failed: " + old_path)
        return asset

    log("Moved: {} -> {}".format(asset.get_name(), target_folder))

    moved_asset = unreal.EditorAssetLibrary.load_asset(new_path)
    return moved_asset if moved_asset else asset


# =========================================================
# MASTER MATERIAL LOADING
# =========================================================

def load_asset_if_exists(path):
    try:
        if unreal.EditorAssetLibrary.does_asset_exist(path):
            return unreal.EditorAssetLibrary.load_asset(path)
    except Exception:
        pass

    return None


def find_material_by_name(asset_names):
    wanted = set(asset_names)

    try:
        paths = unreal.EditorAssetLibrary.list_assets(
            "/Game",
            recursive=True,
            include_folder=False
        )
    except Exception:
        return None

    for path in paths:
        name = path.split("/")[-1].split(".")[0]

        if name not in wanted:
            continue

        asset = unreal.EditorAssetLibrary.load_asset(path)

        if isinstance(asset, unreal.Material):
            log("Found master by name: " + path)
            return asset

    return None


def load_master_material(is_udim):
    if is_udim:
        candidates = MASTER_MATERIAL_UDIM_CANDIDATES
        names = MASTER_UDIM_NAMES
    else:
        candidates = MASTER_MATERIAL_REGULAR_CANDIDATES
        names = MASTER_REGULAR_NAMES

    for path in candidates:
        master = load_asset_if_exists(path)

        if master:
            return master

    master = find_material_by_name(names)

    if master:
        return master

    err(
        "Cannot find {} master material. Checked: {}".format(
            "UDIM" if is_udim else "regular",
            candidates
        )
    )

    return None


# =========================================================
# MATERIAL INSTANCE
# =========================================================

def create_or_load_mi(base_name, output_folder, is_udim):
    """
    base_name should already be canonical.

    This is what makes:
        Bottle
        Bottle_v2
        Bottle_final_v3

    resolve to the same stable MI:
        MI_Bottle
    """
    if not CREATE_MI:
        return None

    canonical_name = canonicalize_asset_name(base_name)

    master = load_master_material(is_udim)

    if not master:
        return None

    if DRY_RUN:
        log(
            "DRY create/load MI: MI_{} in {} parent={}".format(
                canonical_name,
                output_folder,
                master.get_name()
            )
        )
        return None

    if not unreal.EditorAssetLibrary.does_directory_exist(output_folder):
        unreal.EditorAssetLibrary.make_directory(output_folder)

    mi_name = "MI_" + clean_name(canonical_name)
    mi_path = output_folder + "/" + mi_name

    if unreal.EditorAssetLibrary.does_asset_exist(mi_path):
        mi = unreal.EditorAssetLibrary.load_asset(mi_path)

        if not mi:
            err("Cannot load existing MI: " + mi_path)
            return None

        log("Reusing existing MI: " + mi_path)

        if FORCE_EXISTING_MI_PARENT:
            try:
                current_parent = mi.get_editor_property("parent")
            except Exception:
                current_parent = None

            if current_parent != master:
                log(
                    "Switch MI parent: {} -> {}".format(
                        mi.get_name(),
                        master.get_name()
                    )
                )

                try:
                    unreal.MaterialEditingLibrary.set_material_instance_parent(
                        mi,
                        master
                    )
                except Exception:
                    try:
                        mi.set_editor_property("parent", master)
                    except Exception as e:
                        warn(
                            "Cannot switch parent for {}: {}".format(
                                mi.get_name(),
                                e
                            )
                        )

        unreal.MaterialEditingLibrary.update_material_instance(mi)
        safe_save(mi)

        return mi

    factory = unreal.MaterialInstanceConstantFactoryNew()

    mi = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        asset_name=mi_name,
        package_path=output_folder,
        asset_class=unreal.MaterialInstanceConstant,
        factory=factory
    )

    if not mi:
        err("Cannot create MI: " + mi_name)
        return None

    try:
        unreal.MaterialEditingLibrary.set_material_instance_parent(mi, master)
    except Exception:
        try:
            mi.set_editor_property("parent", master)
        except Exception as e:
            err("Cannot set MI parent: " + str(e))
            return None

    unreal.MaterialEditingLibrary.update_material_instance(mi)
    safe_save(mi)

    log("Created MI: {} parent={}".format(mi_path, master.get_name()))

    return mi



def get_texture_override_value(mi, param_name):
    """
    Return the explicit GLOBAL texture override stored on the MIC.
    Returns None when the checkbox/override is not present.

    This checks MaterialInstanceConstant.texture_parameter_values directly,
    rather than only asking for the inherited/effective parameter value.
    """
    try:
        values = mi.get_editor_property("texture_parameter_values")
    except Exception as e:
        warn(
            "Cannot read texture_parameter_values on {}: {}".format(
                mi.get_name(),
                e
            )
        )
        return None

    for entry in values:
        try:
            info = entry.get_editor_property("parameter_info")
            name = str(info.get_editor_property("name"))
            association = info.get_editor_property("association")

            if (
                name == str(param_name)
                and association == unreal.MaterialParameterAssociation.GLOBAL_PARAMETER
            ):
                return entry.get_editor_property("parameter_value")

        except Exception:
            continue

    return None


def force_texture_parameter_override(mi, param_name, texture):
    """
    Ensure the Material Instance contains an EXPLICIT texture override.

    Why this exists:
    In some UE 5.x editor cases, MaterialEditingLibrary can report/call
    SetMaterialInstanceTextureParameterValue without leaving the parameter
    override checkbox enabled in the MIC editor (notably seen with a
    Virtual Normal texture parameter).

    This function writes MaterialInstanceConstant.texture_parameter_values
    directly, which is the array that represents the checked overrides.
    """
    try:
        try:
            mi.modify()
        except Exception:
            pass

        current_values = list(
            mi.get_editor_property("texture_parameter_values")
        )

        new_values = []
        found = False

        for entry in current_values:
            try:
                info = entry.get_editor_property("parameter_info")
                name = str(info.get_editor_property("name"))
                association = info.get_editor_property("association")

                if (
                    name == str(param_name)
                    and association == unreal.MaterialParameterAssociation.GLOBAL_PARAMETER
                ):
                    # Rebuild the struct so the changed value is definitely
                    # written back when the array property is reassigned.
                    parameter_info = unreal.MaterialParameterInfo(
                        name=param_name,
                        association=unreal.MaterialParameterAssociation.GLOBAL_PARAMETER,
                        index=-1
                    )

                    replacement = unreal.TextureParameterValue(
                        parameter_info=parameter_info,
                        parameter_value=texture
                    )

                    new_values.append(replacement)
                    found = True
                    continue

            except Exception as e:
                warn(
                    "Could not inspect an existing texture override on {}: {}".format(
                        mi.get_name(),
                        e
                    )
                )

            new_values.append(entry)

        if not found:
            parameter_info = unreal.MaterialParameterInfo(
                name=param_name,
                association=unreal.MaterialParameterAssociation.GLOBAL_PARAMETER,
                index=-1
            )

            new_values.append(
                unreal.TextureParameterValue(
                    parameter_info=parameter_info,
                    parameter_value=texture
                )
            )

        mi.set_editor_property(
            "texture_parameter_values",
            new_values
        )

        unreal.MaterialEditingLibrary.update_material_instance(mi)
        safe_save(mi)

        stored_value = get_texture_override_value(
            mi,
            param_name
        )

        if stored_value == texture:
            log(
                "FORCED OVERRIDE OK: {} -> {}.{}".format(
                    texture.get_name(),
                    mi.get_name(),
                    param_name
                )
            )
            return True

        warn(
            "Override verification failed: {} -> {}.{}".format(
                texture.get_name(),
                mi.get_name(),
                param_name
            )
        )
        return False

    except Exception as e:
        err(
            "Force texture override failed for {}.{}: {}".format(
                mi.get_name(),
                param_name,
                e
            )
        )
        return False


def assign_textures_to_mi(mi, textures, is_udim):
    """
    Existing texture parameter values are overwritten with the newly chosen textures.

    This is what allows a new texture version to update an existing canonical MI.
    """
    if not mi:
        return

    for tex_type, texture in textures.items():
        param_name = SUFFIX_MAP.get(tex_type)

        if not param_name:
            continue

        tex_is_virtual = is_virtual_texture(texture)

        if is_udim and not tex_is_virtual:
            warn(
                "Skip non-virtual texture for UDIM MI: {}".format(
                    texture.get_name()
                )
            )
            continue

        if (not is_udim) and tex_is_virtual:
            warn(
                "Skip virtual texture for regular MI: {}".format(
                    texture.get_name()
                )
            )
            continue

        try:
            # First try Unreal's normal editor API.
            unreal.MaterialEditingLibrary.set_material_instance_texture_parameter_value(
                mi,
                param_name,
                texture
            )

            unreal.MaterialEditingLibrary.update_material_instance(mi)

            # IMPORTANT:
            # Verify the parameter is actually stored as an explicit MIC override.
            # The effective value can come from the parent even when the checkbox
            # is still OFF, so we inspect texture_parameter_values directly.
            stored_override = get_texture_override_value(
                mi,
                param_name
            )

            if stored_override != texture:
                warn(
                    "Standard setter did not persist explicit override for {}.{}; "
                    "forcing MIC texture override.".format(
                        mi.get_name(),
                        param_name
                    )
                )

                force_texture_parameter_override(
                    mi,
                    param_name,
                    texture
                )
            else:
                log(
                    "UPDATED PARAM + OVERRIDE: {} -> {}.{}".format(
                        texture.get_name(),
                        mi.get_name(),
                        param_name
                    )
                )

        except Exception as e:
            warn(
                "Standard setter failed for {} on {}: {}. "
                "Trying direct MIC override.".format(
                    param_name,
                    mi.get_name(),
                    e
                )
            )

            force_texture_parameter_override(
                mi,
                param_name,
                texture
            )

    # Optional common controls.
    # Safe if the parameters do not exist.
    try:
        unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(
            mi,
            "BaseTint",
            unreal.LinearColor(1, 1, 1, 1)
        )

        unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(
            mi,
            "ColorBoost",
            1.0
        )

        unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(
            mi,
            "EmissiveTint",
            unreal.LinearColor(1, 1, 1, 1)
        )

        unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(
            mi,
            "EmissiveStrength",
            1.0 if "emissive" in textures else 0.0
        )

    except Exception as e:
        warn("Some optional color/emissive params were not set: " + str(e))

    unreal.MaterialEditingLibrary.update_material_instance(mi)
    safe_save(mi)


# =========================================================
# TEXTURE SCANNING
# =========================================================

def get_selected_textures():
    textures = []

    try:
        selected = unreal.EditorUtilityLibrary.get_selected_assets()
    except Exception:
        selected = []

    for asset in selected:
        if isinstance(asset, unreal.Texture2D):
            textures.append(asset)

    return textures


def scan_textures():
    selected = get_selected_textures() if USE_SELECTED_ASSETS_FIRST else []

    if selected:
        log("Using selected Texture2D assets: {}".format(len(selected)))
        return selected

    textures = []
    seen = set()

    for root in TEXTURE_SCAN_ROOTS:
        if not unreal.EditorAssetLibrary.does_directory_exist(root):
            warn("Scan root not found: " + root)
            continue

        paths = unreal.EditorAssetLibrary.list_assets(
            root,
            recursive=True,
            include_folder=False
        )

        for path in paths:
            if path in seen:
                continue

            seen.add(path)

            asset = unreal.EditorAssetLibrary.load_asset(path)

            if isinstance(asset, unreal.Texture2D):
                textures.append(asset)

    log("Scanned Texture2D assets: {}".format(len(textures)))

    return textures


# =========================================================
# GROUP BUILDING
# =========================================================

def build_groups(textures):
    """
    Group by CURRENT DIRECTORY + CANONICAL asset identity.

    Therefore:
        T_Bottle_BaseColor
        T_Bottle_v2_Normal
        T_Bottle_final_v3_ORM

    all belong to canonical group:
        Bottle
    """
    groups = {}

    for texture in textures:
        info = detect_texture(texture.get_name())

        if not info:
            continue

        current_dir = package_dir(texture)
        key = (current_dir, info["canonical_key"])

        if key not in groups:
            target_folder = current_dir + "/" + info["folder_name"]

            groups[key] = {
                "base_name": info["base_name"],
                "canonical_key": info["canonical_key"],
                "source_dir": current_dir,
                "target_folder": target_folder,
                "items": [],
            }

        groups[key]["items"].append({
            "texture": texture,
            "tex_type": info["tex_type"],
            "version": info["version"],
            "raw_base_name": info["raw_base_name"],
            "udim_tile": info["udim_tile"],
            "is_raw_udim_tile": info["is_raw_udim_tile"],
        })

    return groups


def texture_choice_sort_key(item):
    """
    Higher key = preferred.

    Preference:
    1) Highest explicit version, when enabled.
    2) Grouped/virtual texture gets a small tie-break preference.
    3) Stable asset name tie-breaker.
    """
    version = item.get("version")

    if PREFER_HIGHEST_VERSION:
        version_score = version if version is not None else -1
    else:
        version_score = 0

    texture = item["texture"]
    virtual_score = 1 if is_virtual_texture(texture) else 0

    return (
        version_score,
        virtual_score,
        texture.get_name().lower()
    )


def choose_texture_per_type(moved_items):
    by_type = defaultdict(list)

    for item in moved_items:
        by_type[item["tex_type"]].append(item)

    chosen = {}

    for tex_type, item_list in by_type.items():
        item_list.sort(
            key=texture_choice_sort_key,
            reverse=True
        )

        winner = item_list[0]
        chosen[tex_type] = winner["texture"]

        if len(item_list) > 1:
            winner_version = winner.get("version")

            log(
                "Choose {} for {} from {} candidate(s){}".format(
                    winner["texture"].get_name(),
                    tex_type,
                    len(item_list),
                    " | version={}".format(winner_version)
                    if winner_version is not None
                    else ""
                )
            )

            for skipped in item_list[1:]:
                warn(
                    "Older/lower-priority texture skipped for {}: {}".format(
                        tex_type,
                        skipped["texture"].get_name()
                    )
                )

    return chosen


# =========================================================
# GROUP PROCESSING
# =========================================================

def process_group(data):
    base_name = data["base_name"]
    target_folder = data["target_folder"]
    items = data["items"]

    log(
        "PROCESS GROUP: {} | source={} | target={}".format(
            base_name,
            data["source_dir"],
            target_folder
        )
    )

    raw_tiles = [i for i in items if i["is_raw_udim_tile"]]

    if raw_tiles:
        tile_names = [i["texture"].get_name() for i in raw_tiles[:12]]

        warn(
            "Raw UDIM tiles detected for {}: {}".format(
                base_name,
                ", ".join(tile_names)
            )
        )

        warn(
            "These are separate _1001/_1002 tiles, NOT grouped UDIM Virtual Texture assets."
        )

    # Create local canonical folder.
    if MOVE_TEXTURES_INTO_LOCAL_FOLDER and not DRY_RUN:
        if not unreal.EditorAssetLibrary.does_directory_exist(target_folder):
            unreal.EditorAssetLibrary.make_directory(target_folder)
            log("Created folder: " + target_folder)

    elif DRY_RUN:
        log("DRY target folder for {}: {}".format(base_name, target_folder))

    # Apply settings and move textures.
    moved_items = []

    for item in items:
        texture = item["texture"]
        tex_type = item["tex_type"]

        setup_texture(texture, tex_type)

        moved_texture = move_asset_to_folder(
            texture,
            target_folder
        )

        updated_item = dict(item)
        updated_item["texture"] = moved_texture

        moved_items.append(updated_item)

    raw_tiles_after_move = [
        i for i in moved_items
        if i["is_raw_udim_tile"]
    ]

    if raw_tiles_after_move and SKIP_RAW_UDIM_TILE_GROUPS:
        warn(
            "Skip MI for {} because raw UDIM tiles must be "
            "reimported/grouped first.".format(base_name)
        )

        return None, None

    # Choose newest/highest-priority texture for each map type.
    chosen = choose_texture_per_type(moved_items)

    if not chosen:
        warn("No valid textures chosen for group: " + base_name)
        return None, None

    if "normal" not in chosen:
        warn(
            "No Normal map detected for group '{}'. "
            "Supported examples: _Normal, _NormalMap, _NRM, "
            "_Normal_DirectX, _Normal_OpenGL.".format(base_name)
        )

    has_virtual = any(
        is_virtual_texture(texture)
        for texture in chosen.values()
    )

    has_non_virtual = any(
        not is_virtual_texture(texture)
        for texture in chosen.values()
    )

    is_udim = has_virtual

    if has_virtual and has_non_virtual:
        virtual_types = [
            tex_type for tex_type, texture in chosen.items()
            if is_virtual_texture(texture)
        ]
        non_virtual_types = [
            tex_type for tex_type, texture in chosen.items()
            if not is_virtual_texture(texture)
        ]

        warn(
            "Mixed virtual/non-virtual textures in group '{}'. "
            "Virtual types: {} | Non-virtual types: {}. "
            "UDIM master will be used, so non-virtual maps cannot be assigned "
            "to virtual texture parameters.".format(
                base_name,
                ", ".join(virtual_types) if virtual_types else "None",
                ", ".join(non_virtual_types) if non_virtual_types else "None"
            )
        )

    mode_name = "UDIM/Virtual" if is_udim else "Regular"

    log(
        "Group {} mode: {} | folder: {}".format(
            base_name,
            mode_name,
            target_folder
        )
    )

    mi = create_or_load_mi(
        base_name,
        target_folder,
        is_udim
    )

    if mi:
        assign_textures_to_mi(
            mi,
            chosen,
            is_udim
        )

    return mi, is_udim


# =========================================================
# MESH DISCOVERY
# =========================================================

def get_all_meshes():
    meshes = []
    seen = set()

    if not ASSIGN_TO_MESH_ASSET:
        return meshes

    for root in MESH_SCAN_ROOTS:
        if not unreal.EditorAssetLibrary.does_directory_exist(root):
            continue

        paths = unreal.EditorAssetLibrary.list_assets(
            root,
            recursive=True,
            include_folder=False
        )

        for path in paths:
            if path in seen:
                continue

            seen.add(path)

            asset = unreal.EditorAssetLibrary.load_asset(path)

            if isinstance(asset, unreal.StaticMesh):
                meshes.append(asset)

    log("Meshes available for matching: {}".format(len(meshes)))

    return meshes


# =========================================================
# SAFE MESH MATCHING
# =========================================================

def token_set(name):
    canonical = canonical_key(name)

    return {
        token
        for token in canonical.split("_")
        if token
    }


def fuzzy_score(a, b):
    """
    Combine character similarity with token overlap.

    Returns 0..1.
    """
    a_key = canonical_key(a)
    b_key = canonical_key(b)

    if not a_key or not b_key:
        return 0.0

    char_ratio = SequenceMatcher(
        None,
        a_key,
        b_key
    ).ratio()

    a_tokens = token_set(a_key)
    b_tokens = token_set(b_key)

    if a_tokens and b_tokens:
        intersection = len(a_tokens & b_tokens)
        union = len(a_tokens | b_tokens)
        token_ratio = intersection / float(union) if union else 0.0
    else:
        token_ratio = 0.0

    # Character similarity is stronger;
    # token overlap helps names with underscore differences.
    return max(
        char_ratio,
        (char_ratio * 0.70) + (token_ratio * 0.30)
    )


def find_mesh_for_base(base_name, meshes):
    """
    Matching priority:
    1) Exact canonical match.
    2) Manual alias/canonical match.
    3) Safe fuzzy match.
    4) Low-confidence suggestion only.

    Ambiguous fuzzy results are NEVER auto-assigned.
    """
    base_canonical = canonical_key(base_name)

    if not base_canonical:
        return None

    # -----------------------------------------------------
    # 1) EXACT CANONICAL MATCH
    # -----------------------------------------------------

    exact_matches = []

    for mesh in meshes:
        mesh_canonical = canonical_key(mesh.get_name())

        if mesh_canonical == base_canonical:
            exact_matches.append(mesh)

    if len(exact_matches) == 1:
        mesh = exact_matches[0]

        log(
            "Exact canonical mesh match: {} -> {}".format(
                base_name,
                mesh.get_name()
            )
        )

        return mesh

    if len(exact_matches) > 1:
        warn(
            "Ambiguous exact canonical mesh match for {}: {}".format(
                base_name,
                ", ".join(m.get_name() for m in exact_matches[:10])
            )
        )

        return None

    # -----------------------------------------------------
    # 2) SAFE FUZZY FALLBACK
    # -----------------------------------------------------

    if not ENABLE_FUZZY_MESH_MATCH:
        return None

    if len(base_canonical) < MIN_FUZZY_NAME_LENGTH:
        warn(
            "Skip fuzzy mesh matching for short name: {}".format(
                base_name
            )
        )

        return None

    scored = []

    for mesh in meshes:
        mesh_canonical = canonical_key(mesh.get_name())

        if len(mesh_canonical) < MIN_FUZZY_NAME_LENGTH:
            continue

        score = fuzzy_score(
            base_canonical,
            mesh_canonical
        )

        if score >= FUZZY_SUGGEST_THRESHOLD:
            scored.append(
                (score, mesh, mesh_canonical)
            )

    if not scored:
        return None

    scored.sort(
        key=lambda item: item[0],
        reverse=True
    )

    best_score, best_mesh, best_key = scored[0]

    second_score = scored[1][0] if len(scored) > 1 else 0.0

    # High-confidence + not ambiguous.
    if (
        best_score >= FUZZY_AUTO_ASSIGN_THRESHOLD
        and (best_score - second_score) >= FUZZY_AMBIGUITY_MARGIN
    ):
        log(
            "Fuzzy mesh auto-match: {} -> {} | score={:.3f}".format(
                base_name,
                best_mesh.get_name(),
                best_score
            )
        )

        return best_mesh

    # Suggest, but do NOT assign.
    suggestions = []

    for score, mesh, mesh_key in scored[:5]:
        suggestions.append(
            "{} ({:.3f})".format(
                mesh.get_name(),
                score
            )
        )

    warn(
        "Possible mesh match for '{}' but NOT auto-assigned. Suggestions: {}".format(
            base_name,
            ", ".join(suggestions)
        )
    )

    return None


# =========================================================
# MATERIAL ASSIGNMENT
# =========================================================

def assign_mi_to_mesh_asset(mesh, mi):
    if not mesh or not mi or DRY_RUN:
        if DRY_RUN and mesh and mi:
            log(
                "DRY assign {} -> mesh {}".format(
                    mi.get_name(),
                    mesh.get_name()
                )
            )

        return

    try:
        slot_count = len(
            mesh.get_editor_property("static_materials")
        )
    except Exception:
        slot_count = 0

    if slot_count == 0:
        warn(mesh.get_name() + " has no material slots.")
        return

    if slot_count > 1 and not ASSIGN_ALL_MESH_SLOTS:
        warn(
            "Skip multi-slot mesh {} because "
            "ASSIGN_ALL_MESH_SLOTS=False".format(
                mesh.get_name()
            )
        )

        return

    for index in range(slot_count):
        try:
            mesh.set_material(
                index,
                mi
            )
        except Exception as e:
            warn(
                "Set material failed slot {} on {}: {}".format(
                    index,
                    mesh.get_name(),
                    e
                )
            )

    safe_save(mesh)

    log(
        "Assigned {} -> Static Mesh asset {} slot(s)={}".format(
            mi.get_name(),
            mesh.get_name(),
            slot_count
        )
    )


def assign_mi_to_level_actors(mesh, mi):
    if (
        not ASSIGN_TO_LEVEL_ACTORS
        or not mesh
        or not mi
        or DRY_RUN
    ):
        return

    try:
        actors = unreal.EditorLevelLibrary.get_all_level_actors()
    except Exception:
        return

    count = 0

    for actor in actors:
        try:
            components = actor.get_components_by_class(
                unreal.StaticMeshComponent
            )
        except Exception:
            continue

        for component in components:
            try:
                if component.get_static_mesh() == mesh:
                    material_count = component.get_num_materials()

                    for index in range(material_count):
                        component.set_material(
                            index,
                            mi
                        )

                    count += 1

            except Exception:
                continue

    if count > 0:
        log(
            "Assigned {} -> {} actor instance(s) in level".format(
                mi.get_name(),
                count
            )
        )


# =========================================================
# MAIN
# =========================================================

def main():
    textures = scan_textures()
    groups = build_groups(textures)

    if not groups:
        warn("No matching textures found.")
        warn("Expected names like:")
        warn("  T_House_BaseColor")
        warn("  T_House_v2_Normal")
        warn("  T_House_final_v3_ORM")
        warn("  T_tu_ghe_shadingpre_Emissive")
        warn(
            "UDIM grouped assets should usually be named without _1001 in UE, "
            "for example T_House_BaseColor."
        )
        return

    log("Texture groups: {}".format(len(groups)))

    results = []

    for key, data in groups.items():
        mi, is_udim = process_group(data)

        if mi:
            results.append(
                (
                    data["base_name"],
                    mi,
                    is_udim
                )
            )

    if ASSIGN_TO_MESH_ASSET and results:
        meshes = get_all_meshes()

        for base_name, mi, is_udim in results:
            mesh = find_mesh_for_base(
                base_name,
                meshes
            )

            if mesh:
                assign_mi_to_mesh_asset(
                    mesh,
                    mi
                )

                assign_mi_to_level_actors(
                    mesh,
                    mi
                )

            else:
                warn(
                    "No safe mesh match found for: {}".format(
                        base_name
                    )
                )

    log("DONE.")


main()
