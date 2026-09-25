import {
  AdditiveBlending, BackSide, BufferAttribute, BufferGeometry, Group, Mesh, Points,
  ShaderMaterial, SphereGeometry, Vector3,
} from 'three';
import { seededRandom } from '../random';
import { NOISE_GLSL } from '../shaders/noise';

const SKY_VERTEX = /* glsl */ `
varying vec3 vDir;
void main() {
  vDir = position;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const SKY_FRAGMENT = /* glsl */ `
${NOISE_GLSL}
uniform float uTime;
uniform float uLight;
uniform vec3 uHueA;
uniform vec3 uHueB;
uniform vec3 uHueC;
varying vec3 vDir;
void main() {
  vec3 d = normalize(vDir);
  float n = snoise(d * 2.0 + vec3(0.0, 0.0, uTime * 0.01)) * 0.5 + 0.5;
  float n2 = snoise(d * 5.0 - uTime * 0.015) * 0.5 + 0.5;
  float neb = smoothstep(0.45, 1.0, n) * 0.6 + smoothstep(0.6, 1.0, n2) * 0.3;
  vec3 hue = mix(uHueA, uHueB, n2);
  hue = mix(hue, uHueC, smoothstep(0.3, 0.8, n));
  vec3 dark = vec3(0.035, 0.04, 0.055) + hue * neb * 0.35;
  vec3 light = mix(vec3(0.93, 0.94, 0.95), hue, neb * 0.22);
  gl_FragColor = vec4(mix(dark, light, uLight), 1.0);
}
`;

const DUST_VERTEX = /* glsl */ `
attribute float aSize;
attribute vec3 aColor;
uniform float uTime;
uniform float uPixelRatio;
varying vec3 vColor;
varying float vTwinkle;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = aSize * uPixelRatio * (30.0 / -mv.z);
  vColor = aColor;
  vTwinkle = 0.6 + 0.4 * sin(uTime * 1.5 + position.x * 3.1 + position.y * 1.7);
  gl_Position = projectionMatrix * mv;
}
`;

const DUST_FRAGMENT = /* glsl */ `
uniform float uAlpha;
varying vec3 vColor;
varying float vTwinkle;
void main() {
  float d = length(gl_PointCoord - 0.5);
  float a = smoothstep(0.5, 0.0, d) * vTwinkle;
  gl_FragColor = vec4(vColor, a * uAlpha);
}
`;

// Cool cosmos around a warm-listening orb: teal, cyan, blue, purple.
const CLUSTER_HUES: readonly (readonly [number, number, number])[] = [
  [0.18, 0.83, 0.66],
  [0.12, 0.75, 0.88],
  [0.25, 0.5, 0.82],
  [0.5, 0.25, 0.75],
  [0.35, 0.4, 0.85],
];

export interface LandscapeFrame {
  readonly time: number;
  readonly dt: number;
  readonly motion: number;
  readonly light: number;
  readonly pixelRatio: number;
}

export class Landscape {
  readonly group = new Group();
  private readonly sky: Mesh<SphereGeometry, ShaderMaterial>;
  private dust: Points<BufferGeometry, ShaderMaterial>;
  private readonly su = {
    uTime: { value: 0 },
    uLight: { value: 0 },
    uHueA: { value: new Vector3(0.18, 0.83, 0.66) },
    uHueB: { value: new Vector3(0.25, 0.5, 0.82) },
    uHueC: { value: new Vector3(0.5, 0.25, 0.75) },
  };
  private readonly du = {
    uTime: { value: 0 },
    uPixelRatio: { value: 1 },
    uAlpha: { value: 0.85 },
  };
  private readonly seed: number;

  constructor(dustCount: number, seed: number) {
    this.seed = seed;
    this.sky = new Mesh(
      new SphereGeometry(60, 48, 32),
      new ShaderMaterial({
        uniforms: this.su,
        vertexShader: SKY_VERTEX,
        fragmentShader: SKY_FRAGMENT,
        side: BackSide,
        depthWrite: false,
      }),
    );
    this.sky.renderOrder = -1;
    this.dust = this.makeDust(dustCount);
    this.group.add(this.sky, this.dust);
  }

  private makeDust(count: number): Points<BufferGeometry, ShaderMaterial> {
    const rand = seededRandom(this.seed);
    const pos = new Float32Array(count * 3);
    const col = new Float32Array(count * 3);
    const size = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      // A shell between r=8 and r=40, flattened toward a galactic plane.
      const r = 8 + rand() * 32;
      const theta = rand() * Math.PI * 2;
      const phi = Math.acos(2 * rand() - 1);
      pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      pos[i * 3 + 1] = r * Math.cos(phi) * 0.45;
      pos[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta) - 10;
      const hue = CLUSTER_HUES[Math.floor(rand() * CLUSTER_HUES.length)] ?? CLUSTER_HUES[0]!;
      const lift = 0.6 + rand() * 0.4;
      col.set([hue[0] * lift, hue[1] * lift, hue[2] * lift], i * 3);
      size[i] = 0.6 + rand() * 1.8;
    }
    const geo = new BufferGeometry();
    geo.setAttribute('position', new BufferAttribute(pos, 3));
    geo.setAttribute('aColor', new BufferAttribute(col, 3));
    geo.setAttribute('aSize', new BufferAttribute(size, 1));
    return new Points(
      geo,
      new ShaderMaterial({
        uniforms: this.du,
        vertexShader: DUST_VERTEX,
        fragmentShader: DUST_FRAGMENT,
        transparent: true,
        blending: AdditiveBlending,
        depthWrite: false,
      }),
    );
  }

  setDustCount(count: number): void {
    this.group.remove(this.dust);
    this.dust.geometry.dispose();
    this.dust.material.dispose();
    this.dust = this.makeDust(count);
    this.group.add(this.dust);
  }

  update(f: LandscapeFrame): void {
    const t = f.time * f.motion;
    this.su.uTime.value = t;
    this.su.uLight.value = f.light;
    this.du.uTime.value = t;
    this.du.uPixelRatio.value = f.pixelRatio;
    // Additive dust washes out on a light sky; fade it rather than invert it.
    this.du.uAlpha.value = 0.85 * (1 - f.light * 0.7);
    this.dust.rotation.y += f.dt * f.motion * 0.01;
  }

  dispose(): void {
    this.sky.geometry.dispose();
    this.sky.material.dispose();
    this.dust.geometry.dispose();
    this.dust.material.dispose();
  }
}
