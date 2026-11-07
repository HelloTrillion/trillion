import {
  AdditiveBlending, BackSide, NormalBlending, Group, IcosahedronGeometry, Mesh, MeshBasicMaterial,
  ShaderMaterial, SphereGeometry, TorusGeometry, Vector3,
} from 'three';
import type { Levels } from '../audio/levels';
import { NOISE_GLSL } from '../shaders/noise';
import type { OrbParams } from '../states';
import { NO_NOVA, type NovaFrame } from '../supernova';

// Shaders match the predecessor's desktop orb, which has been tuned in daily use:
// a calm surface at rest (displacement scales with breath and audio), a thin
// fresnel edge, and a soft back-face glow.
const ORB_VERTEX = /* glsl */ `
${NOISE_GLSL}
uniform float uTime;
uniform float uNoiseSpeed;
uniform float uAmp;
uniform float uAudio;
uniform float uBass;
uniform float uBreath;
varying vec3 vNormal;
varying vec3 vPosition;
varying float vDisp;
void main() {
  vNormal = normalize(normalMatrix * normal);
  float n1 = snoise(position * 1.5 + uTime * uNoiseSpeed * 0.3) * 0.5;
  float n2 = snoise(position * 3.0 + uTime * uNoiseSpeed * 0.5) * 0.3;
  float n3 = snoise(position * 6.0 + uTime * uNoiseSpeed * 1.8) * 0.2 * uAudio;
  float noiseVal = n1 + n2 + n3;
  float breathDisp = uBreath * 0.04;
  float audioDisp = uAudio * uAmp * 0.55;
  float bassDisp = uBass * uAmp * 0.35;
  float bassPush = uBass * 0.08 * sin(uTime * 2.5 + length(position) * 3.0);
  float d = clamp(noiseVal * (breathDisp + audioDisp + bassDisp + 0.02) + bassPush, -0.45, 0.45);
  vec3 displaced = position + normal * d;
  vDisp = d;
  vec4 mv = modelViewMatrix * vec4(displaced, 1.0);
  vPosition = mv.xyz;
  gl_Position = projectionMatrix * mv;
}
`;

const ORB_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform vec3 uColor2;
uniform float uMix;
uniform float uOpacity;
uniform float uFresnel;
varying vec3 vNormal;
varying vec3 vPosition;
varying float vDisp;
void main() {
  vec3 viewDir = normalize(-vPosition);
  float fresnel = pow(1.0 - abs(dot(viewDir, vNormal)), uFresnel);
  vec3 base = mix(uColor, uColor2, uMix);
  float dispTint = smoothstep(-0.05, 0.25, vDisp);
  vec3 col = base * (0.1 + fresnel * 0.9 + dispTint * 0.25);
  gl_FragColor = vec4(col, uOpacity * (0.2 + fresnel * 0.8));
}
`;

const GLOW_VERTEX = /* glsl */ `
varying vec3 vNormal;
varying vec3 vPosition;
void main() {
  vNormal = normalize(normalMatrix * normal);
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  vPosition = mv.xyz;
  gl_Position = projectionMatrix * mv;
}
`;

const GLOW_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform float uOpacity;
varying vec3 vNormal;
varying vec3 vPosition;
void main() {
  vec3 viewDir = normalize(-vPosition);
  float fresnel = pow(1.0 - abs(dot(viewDir, vNormal)), 2.5);
  gl_FragColor = vec4(uColor, uOpacity * fresnel * 0.6);
}
`;

interface Ring {
  readonly mesh: Mesh<TorusGeometry, MeshBasicMaterial>;
  readonly comet: Mesh<SphereGeometry, MeshBasicMaterial>;
  readonly spin: number;
  readonly orbit: number;
  readonly radius: number;
  angle: number;
}

/** Per-frame inputs, already eased and smoothed by the caller. */
export interface OrbFrame {
  readonly params: OrbParams;
  readonly levels: Levels;
  readonly time: number;
  readonly dt: number;
  readonly flare: number;
  readonly motion: number;
  /** A celebration under way (GAL-9); nothing when absent. */
  readonly nova?: NovaFrame;
}

// A plain ring spinning in its own plane looks still, so each carries a small
// light that orbits it (`orbit`, rad/s) while processing: that is what shows work.
const RING_SPECS = [
  { radius: 1.55, tilt: [1.2, 0.3], spin: 0.21, orbit: 0.7 },
  { radius: 1.65, tilt: [1.45, -0.5], spin: -0.14, orbit: -0.5 },
  { radius: 1.75, tilt: [1.05, 0.9], spin: 0.09, orbit: 0.4 },
] as const;

export class Orb {
  readonly group = new Group();
  private readonly core: Mesh<IcosahedronGeometry, ShaderMaterial>;
  private readonly glow: Mesh<IcosahedronGeometry, ShaderMaterial>;
  private readonly rings: Ring[] = [];
  private readonly u = {
    uTime: { value: 0 },
    uNoiseSpeed: { value: 0 },
    uAmp: { value: 0 },
    uAudio: { value: 0 },
    uBass: { value: 0 },
    uBreath: { value: 0 },
    uColor: { value: new Vector3() },
    uColor2: { value: new Vector3() },
    uMix: { value: 0 },
    uOpacity: { value: 0 },
    uFresnel: { value: 3 },
  };
  private readonly gu = {
    uColor: { value: new Vector3() },
    uOpacity: { value: 0 },
  };
  // The Supernova's shock wave: a fresnel shell that widens and fades (GAL-9).
  private readonly wave: Mesh<IcosahedronGeometry, ShaderMaterial>;
  private readonly wu = {
    uColor: { value: new Vector3() },
    uOpacity: { value: 0 },
  };
  private spinAngle = 0;

  constructor(detail: number) {
    this.core = new Mesh(
      new IcosahedronGeometry(1.0, detail),
      new ShaderMaterial({
        uniforms: this.u,
        vertexShader: ORB_VERTEX,
        fragmentShader: ORB_FRAGMENT,
        wireframe: true,
        transparent: true,
        depthWrite: false,
      }),
    );
    this.glow = new Mesh(
      new IcosahedronGeometry(1.3, 12),
      new ShaderMaterial({
        uniforms: this.gu,
        vertexShader: GLOW_VERTEX,
        fragmentShader: GLOW_FRAGMENT,
        side: BackSide,
        blending: AdditiveBlending,
        transparent: true,
        depthWrite: false,
      }),
    );
    this.wave = new Mesh(
      new IcosahedronGeometry(1.0, 6),
      new ShaderMaterial({
        uniforms: this.wu,
        vertexShader: GLOW_VERTEX,
        fragmentShader: GLOW_FRAGMENT,
        blending: AdditiveBlending,
        transparent: true,
        depthWrite: false,
      }),
    );
    this.wave.visible = false;
    this.group.add(this.glow, this.core, this.wave);

    for (const spec of RING_SPECS) {
      const mesh = new Mesh(
        new TorusGeometry(spec.radius, 0.004, 8, 256),
        new MeshBasicMaterial({
          transparent: true,
          opacity: 0,
          blending: AdditiveBlending,
          depthWrite: false,
        }),
      );
      mesh.rotation.set(spec.tilt[0], spec.tilt[1], 0);
      const comet = new Mesh(
        new SphereGeometry(0.02, 12, 8),
        new MeshBasicMaterial({
          transparent: true,
          opacity: 0,
          blending: AdditiveBlending,
          depthWrite: false,
        }),
      );
      mesh.add(comet);
      this.rings.push({ mesh, comet, spin: spec.spin, orbit: spec.orbit, radius: spec.radius, angle: 0 });
      this.group.add(mesh);
    }
  }

  /** Additive glow washes to white on a light sky; blend it normally there. */
  setTheme(light: boolean): void {
    const blending = light ? NormalBlending : AdditiveBlending;
    for (const m of [this.glow.material, this.wave.material]) {
      if (m.blending !== blending) {
        m.blending = blending;
        m.needsUpdate = true;
      }
    }
  }

  setDetail(detail: number): void {
    this.core.geometry.dispose();
    this.core.geometry = new IcosahedronGeometry(1.0, detail);
  }

  update(f: OrbFrame): void {
    const { params: p, levels, time, dt, flare, motion } = f;
    const nova = f.nova ?? NO_NOVA;
    const t = time; // already scaled by motion in index.ts

    // Processing: the colour mix swings on a 2s cycle, with a pulse each cycle.
    const cycle = 0.5 + 0.5 * Math.sin(t * 1.5);
    const pulse = p.colorOscillation * Math.pow(Math.max(0, Math.sin(t * Math.PI)), 8) * 0.05;

    this.u.uTime.value = t;
    this.u.uNoiseSpeed.value = p.noiseSpeed;
    this.u.uAmp.value = p.displacementAmp * (1 + flare * 0.6) * (0.5 + 0.5 * motion);
    this.u.uAudio.value = levels.level;
    this.u.uBass.value = levels.bass;
    this.u.uBreath.value = 0.5 + 0.5 * Math.sin(t * 0.7);
    this.u.uColor.value.set(p.color[0], p.color[1], p.color[2]);
    this.u.uColor2.value.set(p.colorSecondary[0], p.colorSecondary[1], p.colorSecondary[2]);
    this.u.uMix.value = Math.min(1, p.colorMix + p.colorOscillation * cycle);
    this.u.uOpacity.value = p.opacity;
    this.u.uFresnel.value = p.fresnelPower;

    this.gu.uColor.value.copy(this.u.uColor.value);
    this.gu.uOpacity.value = (p.glowOpacity + levels.level * 0.25) * (1 + flare * 2 + nova.glow * 2.5);

    const scale = 1 + levels.level * 0.12 + levels.bass * 0.08 + flare * 0.1 + pulse + nova.swell;
    this.core.scale.setScalar(scale);
    this.glow.scale.setScalar(1 + levels.level * 0.2 + levels.bass * 0.12 + flare * 0.1 + nova.swell * 1.5);

    // The wave takes her colour, washed toward white as the burst is brightest.
    this.wave.visible = nova.waveOpacity > 0.005 && nova.wave > 0;
    if (this.wave.visible) {
      const w = Math.min(1, nova.glow * 0.5);
      this.wu.uColor.value.set(p.color[0] + (1 - p.color[0]) * w, p.color[1] + (1 - p.color[1]) * w, p.color[2] + (1 - p.color[2]) * w);
      this.wu.uOpacity.value = nova.waveOpacity * 1.6;
      this.wave.scale.setScalar(nova.wave);
    }

    this.spinAngle += dt * motion * (p.rotationSpeed + levels.level * 0.4);
    this.core.rotation.y = this.spinAngle;
    this.core.rotation.x = this.spinAngle * 0.3;
    this.glow.rotation.copy(this.core.rotation);

    for (const ring of this.rings) {
      ring.mesh.material.opacity = p.ringOpacity;
      ring.mesh.material.color.setRGB(p.colorSecondary[0], p.colorSecondary[1], p.colorSecondary[2]);
      ring.mesh.rotation.z += dt * motion * ring.spin;
      ring.mesh.visible = p.ringOpacity > 0.01;
      ring.angle += dt * motion * ring.orbit;
      ring.comet.position.set(ring.radius * Math.cos(ring.angle), ring.radius * Math.sin(ring.angle), 0);
      ring.comet.material.opacity = Math.min(1, p.ringOpacity * 1.6);
      ring.comet.material.color.copy(ring.mesh.material.color);
    }
  }

  dispose(): void {
    this.core.geometry.dispose();
    this.core.material.dispose();
    this.glow.geometry.dispose();
    this.glow.material.dispose();
    this.wave.geometry.dispose();
    this.wave.material.dispose();
    for (const r of this.rings) {
      r.mesh.geometry.dispose();
      r.mesh.material.dispose();
      r.comet.geometry.dispose();
      r.comet.material.dispose();
    }
  }
}
