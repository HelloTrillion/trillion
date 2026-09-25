import {
  AdditiveBlending, BufferAttribute, BufferGeometry, Group, LineSegments, Mesh,
  NormalBlending, OrthographicCamera, PerspectiveCamera, PlaneGeometry, Points, Scene,
  ShaderMaterial, Vector2, Vector3, type Blending, type WebGLRenderer,
} from 'three';
import { seededRandom } from '../random';

/*
 * The landscape behind the orb, matching the predecessor's desktop galaxy:
 * a screen-space nebula sky with twinkling stars, then a slowly turning
 * "neural network" of clustered glowing nodes joined by faint lines, in
 * front of drifting dust. Drawn by the same renderer as the orb (GAL-4):
 * sky pass, particle pass, then the orb pass on top.
 */

const SKY_VERTEX = /* glsl */ `
varying vec2 vUv;
void main() { vUv = uv; gl_Position = vec4(position.xy, 0.9999, 1.0); }
`;

const SKY_FRAGMENT = /* glsl */ `
precision highp float;
varying vec2 vUv;
uniform float uTime;
uniform vec2 uOrbPos;
uniform vec3 uOrbColor;
uniform float uOrbGlow;
uniform float uVoiceBright;
uniform float uNebulaBright;
uniform float uLight;
uniform float uAspect;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vnoise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  mat2 rot = mat2(0.8, -0.6, 0.6, 0.8);
  for (int i = 0; i < 5; i++) { v += a * vnoise(p); p = rot * p * 2.1; a *= 0.48; }
  return v;
}
void main() {
  vec2 uv = vUv;
  float edgeDist = length(uv - 0.5);
  float t = uTime;
  float m1 = smoothstep(0.40, 0.68, fbm(uv * 3.0 + vec2(t * 0.002, t * 0.001)));
  float m2 = smoothstep(0.45, 0.70, fbm(uv * 2.5 + vec2(50.0, 30.0) + vec2(-t * 0.001, t * 0.0015)));
  float m3 = smoothstep(0.48, 0.72, fbm(uv * 4.0 + vec2(100.0) + vec2(t * 0.0008, -t * 0.0005)));
  float orbDist = length((uv - uOrbPos) * vec2(1.0, 1.2));

  // Dark: deep space, teal/purple/blue nebula, orb bloom, two star layers, vignette.
  vec3 dark = mix(vec3(0.012, 0.016, 0.04), vec3(0.004, 0.006, 0.018), edgeDist);
  dark += vec3(0.0, 0.08, 0.06) * m1 * uNebulaBright;
  dark += vec3(0.05, 0.01, 0.08) * m2 * uNebulaBright * 0.7;
  dark += vec3(0.02, 0.03, 0.07) * m3 * uNebulaBright * 0.5;
  dark += uOrbColor * exp(-orbDist * orbDist / 0.18) * uOrbGlow * 0.20;
  dark += uOrbColor * exp(-orbDist * orbDist / 0.06) * uOrbGlow * 0.30;
  dark += vec3(0.7, 0.95, 0.9) * exp(-orbDist * orbDist / 0.010) * uOrbGlow * 0.18;
  dark += uOrbColor * exp(-orbDist * orbDist / 0.12) * uVoiceBright * 0.30;
  // Star grids are square in screen space, whatever the window's shape.
  vec2 suv = vec2(uv.x * uAspect, uv.y);
  vec2 sg1 = floor(suv * vec2(250.0));
  float sv1 = hash(sg1);
  float star1 = step(0.992, sv1) * (0.3 + 0.7 * hash(sg1 + 100.0));
  star1 *= 0.5 + 0.5 * sin(t * (1.2 + hash(sg1 + 200.0) * 3.0) + sv1 * 40.0);
  dark += vec3(0.7, 0.8, 1.0) * star1 * 0.45;
  vec2 sg2 = floor(suv * vec2(65.0));
  float sv2 = hash(sg2 + 500.0);
  float sd2 = length(fract(suv * vec2(65.0)) - 0.5);
  float star2 = step(0.984, sv2) * exp(-sd2 * sd2 / 0.025) * (0.4 + 0.6 * hash(sg2 + 600.0));
  star2 *= 0.6 + 0.4 * sin(t * (0.8 + hash(sg2 + 700.0) * 2.0) + sv2 * 30.0);
  dark += vec3(0.8, 0.85, 1.0) * star2 * 0.55;
  dark *= 1.0 - 0.40 * pow(edgeDist * 1.5, 2.0);

  // Light: a pale dawn sky with the same nebula shapes as soft tints, no stars.
  vec3 light = mix(vec3(0.965, 0.968, 0.955), vec3(0.90, 0.915, 0.93), edgeDist);
  light -= vec3(0.10, 0.02, 0.05) * m1 * uNebulaBright * 0.5;
  light -= vec3(0.03, 0.08, 0.01) * m2 * uNebulaBright * 0.4;
  light += uOrbColor * exp(-orbDist * orbDist / 0.06) * uOrbGlow * 0.12;

  gl_FragColor = vec4(mix(dark, light, uLight), 1.0);
}
`;

// Round soft sprite: bright core, half at 30% radius, gone at the edge.
const POINT_VERTEX = /* glsl */ `
attribute vec3 aColor;
uniform float uSize;
uniform float uScale;
uniform float uLight;
varying vec3 vColor;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = uSize * uScale * mix(1.0, 0.6, uLight) / max(0.5, -mv.z);
  vColor = aColor;
  gl_Position = projectionMatrix * mv;
}
`;

const POINT_FRAGMENT = /* glsl */ `
uniform float uOpacity;
uniform float uLight;
varying vec3 vColor;
void main() {
  float r = length(gl_PointCoord - 0.5) * 2.0;
  float a = r < 0.3 ? mix(1.0, 0.5, r / 0.3) : mix(0.5, 0.0, (r - 0.3) / 0.7);
  if (a <= 0.0) discard;
  // On a pale sky the glow reads as ink; keep it lighter and smaller.
  gl_FragColor = vec4(mix(vColor, vColor * 0.7, uLight), a * uOpacity * mix(1.0, 0.45, uLight));
}
`;

const LINE_VERTEX = /* glsl */ `
attribute vec3 aColor;
varying vec3 vColor;
void main() {
  vColor = aColor;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const LINE_FRAGMENT = /* glsl */ `
uniform float uOpacity;
uniform float uLight;
varying vec3 vColor;
void main() { gl_FragColor = vec4(mix(vColor, vColor * 0.5, uLight), uOpacity); }
`;

const CLUSTER_HUES: readonly (readonly [number, number, number])[] = [
  [0.18, 0.83, 0.66], // teal (orb-adjacent)
  [0.12, 0.75, 0.88], // cyan
  [0.25, 0.5, 0.82], // blue
  [0.5, 0.25, 0.75], // purple
  [0.19, 0.88, 0.63], // green-teal
  [0.35, 0.4, 0.85], // indigo
  [0.15, 0.65, 0.78], // blue-teal
  [0.4, 0.3, 0.8], // violet
];
const STRAY_HUE = [0.3, 0.55, 0.7] as const;
const DUST_HUE = [0.53, 0.6, 0.8] as const;
const CLUSTER_COUNT = 8;
const MAX_LINK = 10;

export interface LandscapeFrame {
  readonly time: number;
  readonly dt: number;
  readonly motion: number;
  readonly light: number;
  /** Speaking/processing loudness 0..1: brightens and swells the network. */
  readonly voice: number;
  readonly bass: number;
  /** Current orb colour, for the sky's bloom. */
  readonly orbColor: readonly [number, number, number];
  /** Drawing-buffer height / 2, for perspective point sizes. */
  readonly pointScale: number;
}

interface PointUniforms {
  uSize: { value: number };
  uScale: { value: number };
  uOpacity: { value: number };
  uLight: { value: number };
}

function pointMaterial(u: PointUniforms): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: u as unknown as Record<string, { value: unknown }>,
    vertexShader: POINT_VERTEX,
    fragmentShader: POINT_FRAGMENT,
    transparent: true,
    blending: AdditiveBlending,
    depthWrite: false,
  });
}

function spherePoint(rand: () => number, r: number): [number, number, number] {
  const theta = rand() * Math.PI * 2;
  const phi = Math.acos(2 * rand() - 1);
  return [r * Math.sin(phi) * Math.cos(theta), r * Math.sin(phi) * Math.sin(theta), r * Math.cos(phi)];
}

export class Landscape {
  readonly skyScene = new Scene();
  readonly skyCamera = new OrthographicCamera(-1, 1, 1, -1, 0, 1);
  readonly scene = new Scene();
  readonly camera = new PerspectiveCamera(60, 1, 0.1, 250);

  private readonly sky: Mesh<PlaneGeometry, ShaderMaterial>;
  private readonly network = new Group();
  private dust: Points<BufferGeometry, ShaderMaterial> | null = null;
  private nodes: Points<BufferGeometry, ShaderMaterial> | null = null;
  private lines: LineSegments<BufferGeometry, ShaderMaterial> | null = null;
  private readonly seed: number;
  private blending: Blending = AdditiveBlending;

  private readonly su = {
    uTime: { value: 0 },
    uOrbPos: { value: new Vector2(0.5, 0.5) },
    uOrbColor: { value: new Vector3(0.07, 0.33, 0.26) },
    uOrbGlow: { value: 0.25 },
    uVoiceBright: { value: 0 },
    uNebulaBright: { value: 0.55 },
    uLight: { value: 0 },
    uAspect: { value: 1 },
  };
  private readonly dustU: PointUniforms = {
    uSize: { value: 1.2 }, uScale: { value: 400 }, uOpacity: { value: 0.2 }, uLight: { value: 0 },
  };
  private readonly nodeU: PointUniforms = {
    uSize: { value: 2.8 }, uScale: { value: 400 }, uOpacity: { value: 0.65 }, uLight: { value: 0 },
  };
  private readonly lineU = { uOpacity: { value: 0.12 }, uLight: { value: 0 } };

  constructor(dustCount: number, nodeCount: number, seed: number) {
    this.seed = seed;
    this.sky = new Mesh(
      new PlaneGeometry(2, 2),
      new ShaderMaterial({
        uniforms: this.su,
        vertexShader: SKY_VERTEX,
        fragmentShader: SKY_FRAGMENT,
        depthWrite: false,
        depthTest: false,
      }),
    );
    this.skyScene.add(this.sky);
    this.camera.position.set(0, 0, 42);
    this.camera.lookAt(0, 0, 0);
    this.scene.add(this.network);
    this.build(dustCount, nodeCount);
  }

  /** (Re)build dust and network. Seeded, so a lower count is the same galaxy, thinner. */
  setCounts(dustCount: number, nodeCount: number): void {
    const spin = this.network.rotation.y;
    this.disposeParticles();
    this.build(dustCount, nodeCount);
    this.network.rotation.y = spin;
  }

  private build(dustCount: number, nodeCount: number): void {
    const rand = seededRandom(this.seed);

    const dPos = new Float32Array(dustCount * 3);
    const dCol = new Float32Array(dustCount * 3);
    for (let i = 0; i < dustCount; i++) {
      dPos.set(spherePoint(rand, 20 + rand() * 70), i * 3);
      dCol.set(DUST_HUE, i * 3);
    }
    this.dust = new Points(this.geometry(dPos, dCol), pointMaterial(this.dustU));
    this.scene.add(this.dust);

    const nPos = new Float32Array(nodeCount * 3);
    const nCol = new Float32Array(nodeCount * 3);
    const centers = Array.from({ length: CLUSTER_COUNT }, () => spherePoint(rand, 13 + rand() * 12));
    let n = 0;
    for (let c = 0; c < CLUSTER_COUNT && n < nodeCount; c++) {
      const count = 6 + Math.floor(rand() * 3);
      const center = centers[c] ?? [0, 0, 0];
      const spread = 3 + rand() * 4;
      const hue = CLUSTER_HUES[c] ?? STRAY_HUE;
      for (let k = 0; k < count && n < nodeCount; k++, n++) {
        nPos.set(center.map((v) => v + (rand() - 0.5) * spread * 2), n * 3);
        nCol.set(hue, n * 3);
      }
    }
    for (; n < nodeCount; n++) {
      nPos.set(spherePoint(rand, 8 + rand() * 22), n * 3);
      nCol.set(STRAY_HUE, n * 3);
    }
    this.nodes = new Points(this.geometry(nPos, nCol), pointMaterial(this.nodeU));

    const lPos: number[] = [];
    const lCol: number[] = [];
    for (let i = 0; i < nodeCount; i++) {
      for (let j = i + 1; j < nodeCount; j++) {
        const dx = (nPos[i * 3] ?? 0) - (nPos[j * 3] ?? 0);
        const dy = (nPos[i * 3 + 1] ?? 0) - (nPos[j * 3 + 1] ?? 0);
        const dz = (nPos[i * 3 + 2] ?? 0) - (nPos[j * 3 + 2] ?? 0);
        if (dx * dx + dy * dy + dz * dz < MAX_LINK * MAX_LINK) {
          lPos.push(...nPos.subarray(i * 3, i * 3 + 3), ...nPos.subarray(j * 3, j * 3 + 3));
          lCol.push(...nCol.subarray(i * 3, i * 3 + 3), ...nCol.subarray(j * 3, j * 3 + 3));
        }
      }
    }
    this.lines = new LineSegments(
      this.geometry(new Float32Array(lPos), new Float32Array(lCol)),
      new ShaderMaterial({
        uniforms: this.lineU,
        vertexShader: LINE_VERTEX,
        fragmentShader: LINE_FRAGMENT,
        transparent: true,
        blending: AdditiveBlending,
        depthWrite: false,
      }),
    );
    this.network.add(this.nodes, this.lines);
    this.applyBlending();
  }

  private geometry(pos: Float32Array, col: Float32Array): BufferGeometry {
    const g = new BufferGeometry();
    g.setAttribute('position', new BufferAttribute(pos, 3));
    g.setAttribute('aColor', new BufferAttribute(col, 3));
    return g;
  }

  /** Additive light vanishes on a pale sky; the light theme draws particles normally. */
  setTheme(light: boolean): void {
    this.blending = light ? NormalBlending : AdditiveBlending;
    this.applyBlending();
  }

  private applyBlending(): void {
    for (const obj of [this.dust, this.nodes, this.lines]) {
      if (!obj || obj.material.blending === this.blending) continue;
      obj.material.blending = this.blending;
      obj.material.needsUpdate = true;
    }
  }

  setAspect(aspect: number): void {
    this.su.uAspect.value = aspect;
    this.camera.aspect = aspect;
    this.camera.updateProjectionMatrix();
  }

  update(f: LandscapeFrame): void {
    const t = f.time;
    const k = Math.min(1, f.dt * 6); // ≈0.1 per frame at 60fps
    const v = f.voice;
    const b = f.bass;

    this.su.uTime.value = t;
    this.su.uLight.value = f.light;
    this.su.uOrbColor.value.set(f.orbColor[0], f.orbColor[1], f.orbColor[2]);
    this.su.uOrbGlow.value += (0.25 + v + b * 0.4 - this.su.uOrbGlow.value) * k;
    this.su.uVoiceBright.value += (v - this.su.uVoiceBright.value) * k;
    this.su.uNebulaBright.value += (0.55 + v * 0.45 - this.su.uNebulaBright.value) * k;

    this.network.rotation.y += f.dt * 60 * f.motion * (0.0004 + v * 0.004);
    this.network.rotation.x = Math.sin(t * 0.05) * 0.08 + v * Math.sin(t * 1.5) * 0.06;
    const scale = this.network.scale.x + (1 + v * 0.08 + b * 0.12 - this.network.scale.x) * k * 0.6;
    this.network.scale.setScalar(scale);

    this.lineU.uOpacity.value += (0.12 + v * 0.55 - this.lineU.uOpacity.value) * k;
    this.nodeU.uOpacity.value += (0.65 + v * 0.35 - this.nodeU.uOpacity.value) * k;
    this.nodeU.uSize.value += (2.8 + v * 3.5 + b * 2 - this.nodeU.uSize.value) * k;
    this.dustU.uOpacity.value += (0.2 + v * 0.35 - this.dustU.uOpacity.value) * k;
    for (const u of [this.dustU, this.nodeU, this.lineU]) u.uLight.value = f.light;
    this.dustU.uScale.value = f.pointScale;
    this.nodeU.uScale.value = f.pointScale;

    // Slow camera drift, a shake and a push-in while she speaks.
    const m = f.motion;
    this.camera.position.x = Math.sin(t * 0.007) * 2 * m + v * Math.sin(t * 8) * 0.3 * m;
    this.camera.position.y = Math.cos(t * 0.005) * 1.5 * m + v * Math.cos(t * 6.5) * 0.2 * m;
    this.camera.position.z += (42 - v * 3 - this.camera.position.z) * k * 0.4;
  }

  render(renderer: WebGLRenderer): void {
    renderer.render(this.skyScene, this.skyCamera);
    renderer.render(this.scene, this.camera);
  }

  private disposeParticles(): void {
    for (const obj of [this.dust, this.nodes, this.lines]) {
      if (!obj) continue;
      obj.removeFromParent();
      obj.geometry.dispose();
      obj.material.dispose();
    }
    this.dust = this.nodes = this.lines = null;
  }

  dispose(): void {
    this.disposeParticles();
    this.sky.geometry.dispose();
    this.sky.material.dispose();
  }
}
