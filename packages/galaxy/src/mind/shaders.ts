/**
 * The Living Mind's shaders: where its look lives. A node is a reverse-fresnel bead
 * of light (white-hot centre bleeding to a coloured rim) that breathes on its own
 * phase and burns brighter the fresher it is. Bloom turns it into light.
 */

/** A single-mesh glow sphere: her star, a helper. */
export const NODE_VERTEX = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vViewPosition = -mvPosition.xyz;
    gl_Position = projectionMatrix * mvPosition;
  }
`;

export const NODE_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  uniform float uBoost;
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  void main() {
    vec3 viewDir = normalize(vViewPosition);
    float facing = max(dot(vNormal, viewDir), 0.0);
    float core = pow(facing, 2.5);
    float rim = pow(1.0 - facing, 2.0);
    vec3 col = mix(uColor, vec3(1.0), core * 0.85);
    col += uColor * rim * 1.4;
    col *= 1.0 + uBoost;
    // The rim fades out over the last sliver, so the silhouette is soft, not a cut disc.
    float edge = smoothstep(0.0, 0.2, facing);
    gl_FragColor = vec4(col, (core * 0.95 + rim * 0.6 * edge) * uOpacity);
  }
`;

/**
 * The back-side outer halo of a glow sphere. Seen from inside, a back face's normal
 * points away, so the usual max(dot) is zero everywhere and the halo was a flat,
 * hard-edged disc. abs(dot) is 1 at the halo's centre and 0 at its edge: a glow that
 * falls smoothly to nothing.
 */
export const HALO_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  void main() {
    vec3 viewDir = normalize(vViewPosition);
    float glow = pow(abs(dot(vNormal, viewDir)), 2.5);
    gl_FragColor = vec4(uColor * glow * 1.6, glow * uOpacity);
  }
`;

/**
 * The membrane. A back-side sphere flips its normals, so max(dot) is zero everywhere
 * and the usual fresnel reads as a solid disk; abs() lights only the silhouette.
 */
export const MEMBRANE_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  void main() {
    vec3 viewDir = normalize(vViewPosition);
    float rim = pow(1.0 - abs(dot(vNormal, viewDir)), 3.0);
    gl_FragColor = vec4(uColor * rim * 2.0, rim * uOpacity);
  }
`;

const BREATH = /* glsl */ `
  float breathWave(float t, float phase) {
    return sin(t * (3.5 + 2.5 * fract(phase * 0.7)) + phase) * uBreath;
  }
`;

/*
 * The many small nodes, one instanced mesh per region. three injects instanceMatrix
 * and instanceColor into a ShaderMaterial on an InstancedMesh; declaring them again is
 * a redefinition error. aBoost is a brief brightening (a recall, a finished tool).
 */
export const INST_NODE_VERTEX = /* glsl */ `
  attribute float aPhase;
  attribute float aFreshness;
  attribute float aBoost;
  uniform float uTime;
  uniform float uBreath;
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  varying vec3 vColor;
  varying float vFreshness;
  varying float vBreath;
  varying float vBoost;
  ${BREATH}
  void main() {
    vBreath = breathWave(uTime, aPhase);
    vec4 worldPos = instanceMatrix * vec4(position * (1.0 + 0.06 * vBreath), 1.0);
    vec4 mvPosition = modelViewMatrix * worldPos;
    vNormal = normalize(normalMatrix * (mat3(instanceMatrix) * normal));
    vViewPosition = -mvPosition.xyz;
    #ifdef USE_INSTANCING_COLOR
      vColor = instanceColor;
    #else
      vColor = vec3(1.0);
    #endif
    vFreshness = aFreshness;
    vBoost = aBoost;
    gl_Position = projectionMatrix * mvPosition;
  }
`;

export const INST_NODE_FRAGMENT = /* glsl */ `
  uniform float uOpacity;
  varying vec3 vNormal;
  varying vec3 vViewPosition;
  varying vec3 vColor;
  varying float vFreshness;
  varying float vBreath;
  varying float vBoost;
  void main() {
    vec3 viewDir = normalize(vViewPosition);
    float facing = max(dot(normalize(vNormal), viewDir), 0.0);
    float core = pow(facing, 2.5);
    float rim = pow(1.0 - facing, 2.0);
    vec3 col = mix(vColor, vec3(1.0), core * 0.85);
    col += vColor * rim * 1.4;
    // Fresh memories burn; old ones ember.
    float glow = 0.45 + 0.75 * vFreshness;
    col *= glow * (1.0 + (0.03 + 0.05 * vFreshness) * vBreath) * (1.0 + vBoost);
    float edge = smoothstep(0.0, 0.2, facing);
    gl_FragColor = vec4(col, (core * 0.95 + rim * 0.6 * edge) * uOpacity);
  }
`;

/**
 * The aura round each small node: a camera-facing quad with a soft radial falloff,
 * sharing the node mesh's matrices and colours so a flare swells both together.
 */
export const INST_HALO_VERTEX = /* glsl */ `
  attribute float aPhase;
  attribute float aFreshness;
  attribute float aBoost;
  uniform float uTime;
  uniform float uBreath;
  varying vec2 vUv;
  varying vec3 vColor;
  varying float vGlow;
  ${BREATH}
  void main() {
    float breath = breathWave(uTime, aPhase);
    float s = length(instanceMatrix[0].xyz);
    vec4 mvCenter = modelViewMatrix * vec4(instanceMatrix[3].xyz, 1.0);
    mvCenter.xy += position.xy * s * 3.4 * (1.0 + 0.06 * breath);
    vUv = position.xy * 2.0;
    #ifdef USE_INSTANCING_COLOR
      vColor = instanceColor;
    #else
      vColor = vec3(1.0);
    #endif
    vGlow = (0.45 + 0.75 * aFreshness) * (1.0 + 0.10 * breath) * (1.0 + aBoost * 1.5);
    gl_Position = projectionMatrix * mvCenter;
  }
`;

export const INST_HALO_FRAGMENT = /* glsl */ `
  uniform float uOpacity;
  varying vec2 vUv;
  varying vec3 vColor;
  varying float vGlow;
  void main() {
    float falloff = pow(max(1.0 - length(vUv), 0.0), 2.2);
    gl_FragColor = vec4(vColor * vGlow, falloff * uOpacity);
  }
`;

/** Similarity edges glitter: each edge breathes its alpha on its own phase. */
export const SIM_EDGE_VERTEX = /* glsl */ `
  attribute float aPhase;
  varying float vPhase;
  void main() {
    vPhase = aPhase;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

export const SIM_EDGE_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  uniform float uTime;
  uniform float uShimmer;
  varying float vPhase;
  void main() {
    float wave = 0.94 + 0.31 * sin(uTime * 1.6 + vPhase);
    gl_FragColor = vec4(uColor, uOpacity * mix(1.0, wave, uShimmer));
  }
`;

/**
 * Trunks carry a comet of light. aT is the vertex's place along its curve; the sign
 * of uSpeed is the direction (outward from her, or inward for recall).
 */
export const FLOW_EDGE_VERTEX = /* glsl */ `
  attribute float aT;
  attribute float aPhase;
  attribute float aW;
  varying float vT;
  varying float vPhase;
  varying float vW;
  void main() {
    vT = aT; vPhase = aPhase; vW = aW;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

export const FLOW_EDGE_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  uniform float uTime;
  uniform float uSpeed;
  uniform float uFlow;
  varying float vT;
  varying float vPhase;
  varying float vW;
  void main() {
    float head = fract(uTime * uSpeed + vPhase);
    float d = fract(head - vT);
    float band = exp(-d * 9.0);
    float base = mix(1.0, 0.5, uFlow);
    float energy = base + uFlow * band * 3.0;
    vec3 col = mix(uColor, vec3(1.0), min(band * uFlow * 0.65, 0.65));
    gl_FragColor = vec4(col * energy, uOpacity * (0.35 + 0.65 * vW) * energy);
  }
`;

/** Stars far outside the membrane, twinkling past the bloom threshold. */
export const STAR_VERTEX = /* glsl */ `
  attribute float aPhase;
  attribute float aSize;
  uniform float uTime;
  uniform float uTwinkle;
  varying float vBrightness;
  void main() {
    vBrightness = 0.55 + 0.85 * mix(0.5, 0.5 + 0.5 * sin(uTime * 1.2 + aPhase), uTwinkle);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = aSize * (2200.0 / -mv.z);
  }
`;

export const STAR_FRAGMENT = /* glsl */ `
  varying float vBrightness;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    if (d > 0.5) discard;
    float falloff = smoothstep(0.5, 0.0, d);
    gl_FragColor = vec4(vec3(0.96, 0.98, 1.0) * vBrightness, falloff * vBrightness);
  }
`;
