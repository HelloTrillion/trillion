import {
  AdditiveBlending, BackSide, Group, IcosahedronGeometry, Mesh, MeshBasicMaterial,
  ShaderMaterial, TorusGeometry, Vector3,
} from 'three';
import type { Levels } from '../audio/levels';
import { NOISE_GLSL } from '../shaders/noise';
import type { OrbParams } from '../states';

const ORB_VERTEX = /* glsl */ `
${NOISE_GLSL}
uniform float uTime;
uniform float uNoiseSpeed;
uniform float uAmp;
uniform float uAudio;
uniform float uBass;
uniform float uBreath;
varying vec3 vNormal;
varying vec3 vViewDir;
varying float vDisp;
void main() {
  float t = uTime * uNoiseSpeed;
  vec3 p = position * 1.6;
  float n = snoise(p + t) * 0.5
          + snoise(p * 2.0 + t * 1.3) * 0.25
          + snoise(p * 4.0 + t * 1.7) * 0.125 * (0.4 + uAudio);
  float d = uBreath * 0.04
          + n * uAmp
          + n * uAudio * uAmp * 0.55
          + uBass * uAmp * 0.35 * sin(position.y * 6.0 + uTime * 4.0);
  d = clamp(d, -0.45, 0.45);
  vec3 displaced = position + normal * d;
  vec4 mv = modelViewMatrix * vec4(displaced, 1.0);
  vNormal = normalize(normalMatrix * normal);
  vViewDir = normalize(-mv.xyz);
  vDisp = d;
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
varying vec3 vViewDir;
varying float vDisp;
void main() {
  float fres = pow(1.0 - max(dot(normalize(vNormal), normalize(vViewDir)), 0.0), uFresnel);
  vec3 col = mix(uColor, uColor2, uMix);
  col = mix(col, uColor2, clamp(vDisp * 2.0, 0.0, 1.0) * 0.5);
  col += fres * col * 0.8;
  gl_FragColor = vec4(col, uOpacity * (0.35 + fres * 0.65));
}
`;

const GLOW_VERTEX = /* glsl */ `
varying vec3 vNormal;
void main() {
  vNormal = normalize(normalMatrix * normal);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const GLOW_FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform float uOpacity;
varying vec3 vNormal;
void main() {
  float intensity = pow(max(0.0, 0.72 - dot(vNormal, vec3(0.0, 0.0, 1.0))), 2.0);
  gl_FragColor = vec4(uColor * intensity, intensity * uOpacity * 4.0);
}
`;

interface Ring {
  readonly mesh: Mesh<TorusGeometry, MeshBasicMaterial>;
  readonly spin: number;
}

/** Per-frame inputs, already eased and smoothed by the caller. */
export interface OrbFrame {
  readonly params: OrbParams;
  readonly levels: Levels;
  readonly time: number;
  readonly dt: number;
  readonly flare: number;
  readonly motion: number;
}

const RING_SPECS = [
  { radius: 1.55, tilt: [1.2, 0.3], spin: 0.21 },
  { radius: 1.65, tilt: [1.45, -0.5], spin: -0.14 },
  { radius: 1.75, tilt: [1.05, 0.9], spin: 0.09 },
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
    this.group.add(this.glow, this.core);

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
      this.rings.push({ mesh, spin: spec.spin });
      this.group.add(mesh);
    }
  }

  setDetail(detail: number): void {
    this.core.geometry.dispose();
    this.core.geometry = new IcosahedronGeometry(1.0, detail);
  }

  update(f: OrbFrame): void {
    const { params: p, levels, time, dt, flare, motion } = f;
    const t = time * motion;

    // Processing: the colour mix swings on a 2s cycle, with a pulse each cycle.
    const cycle = 0.5 + 0.5 * Math.sin(t * Math.PI);
    const pulse = p.colorOscillation * Math.pow(Math.max(0, Math.sin(t * Math.PI)), 8) * 0.05;

    this.u.uTime.value = t;
    this.u.uNoiseSpeed.value = p.noiseSpeed;
    this.u.uAmp.value = p.displacementAmp * (1 + flare * 0.6) * (0.5 + 0.5 * motion);
    this.u.uAudio.value = levels.level;
    this.u.uBass.value = levels.bass;
    this.u.uBreath.value = Math.sin(t * 0.8);
    this.u.uColor.value.set(p.color[0], p.color[1], p.color[2]);
    this.u.uColor2.value.set(p.colorSecondary[0], p.colorSecondary[1], p.colorSecondary[2]);
    this.u.uMix.value = Math.min(1, p.colorMix + p.colorOscillation * cycle);
    this.u.uOpacity.value = p.opacity;
    this.u.uFresnel.value = p.fresnelPower;

    this.gu.uColor.value.copy(this.u.uColor.value);
    this.gu.uOpacity.value = p.glowOpacity * (1 + flare * 2);

    const scale = 1 + levels.level * 0.12 + levels.bass * 0.08 + flare * 0.1 + pulse;
    this.core.scale.setScalar(scale);
    this.glow.scale.setScalar(scale * (1 + levels.level * 0.1));

    this.spinAngle += dt * motion * (p.rotationSpeed + levels.level * 0.4);
    this.core.rotation.y = this.spinAngle;
    this.core.rotation.x = this.spinAngle * 0.35;

    for (const ring of this.rings) {
      ring.mesh.material.opacity = p.ringOpacity;
      ring.mesh.material.color.setRGB(p.colorSecondary[0], p.colorSecondary[1], p.colorSecondary[2]);
      ring.mesh.rotation.z += dt * motion * ring.spin;
      ring.mesh.visible = p.ringOpacity > 0.01;
    }
  }

  dispose(): void {
    this.core.geometry.dispose();
    this.core.material.dispose();
    this.glow.geometry.dispose();
    this.glow.material.dispose();
    for (const r of this.rings) {
      r.mesh.geometry.dispose();
      r.mesh.material.dispose();
    }
  }
}
