/**
 * Draws energy arcs (GAL-8) over the orb, in a flat scene measured in CSS pixels.
 * The shapes and timings are in ./arc; this only turns them into meshes.
 */
import {
  BufferAttribute, BufferGeometry, DoubleSide, Mesh, NormalBlending, OrthographicCamera, Scene, ShaderMaterial,
  Vector3, type WebGLRenderer,
} from 'three';
import { ARC_MS, MAX_ARCS, type Point, arcFade, arcHead, arcPoints, ribbon } from './arc';
import type { RGB } from './states';

const VERTEX = /* glsl */ `
attribute float along;
varying float vAlong;
void main() {
  vAlong = along;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

// Mirrors arcGlow in ./arc.
const FRAGMENT = /* glsl */ `
uniform vec3 uColor;
uniform float uHead;
uniform float uFade;
varying float vAlong;
void main() {
  if (vAlong > uHead + 0.04) discard;
  float d = (vAlong - uHead) / 0.06;
  float behind = max(0.0, uHead - vAlong);
  float glow = min(1.0, exp(-d * d) + 0.35 * exp(-behind / 0.25));
  gl_FragColor = vec4(uColor, glow * uFade * 0.7);
}
`;

interface Live {
  readonly mesh: Mesh<BufferGeometry, ShaderMaterial>;
  readonly born: number;
}

export class ArcLayer {
  private readonly scene = new Scene();
  private readonly camera = new OrthographicCamera(0, 1, 0, 1, -1, 1);
  private live: Live[] = [];

  /** The canvas's size in CSS pixels: y grows downward, as on the page. */
  setSize(w: number, h: number): void {
    this.camera.left = 0;
    this.camera.right = w;
    this.camera.top = 0;
    this.camera.bottom = h;
    this.camera.updateProjectionMatrix();
  }

  get active(): boolean {
    return this.live.length > 0;
  }

  add(from: Point, to: Point, color: RGB, now: number): void {
    while (this.live.length >= MAX_ARCS) this.drop(0);
    const shape = ribbon(arcPoints(from, to));
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new BufferAttribute(shape.positions, 3));
    geometry.setAttribute('along', new BufferAttribute(shape.along, 1));
    geometry.setIndex(shape.index);
    const material = new ShaderMaterial({
      uniforms: {
        uColor: { value: new Vector3(...color) },
        uHead: { value: 0 },
        uFade: { value: 1 },
      },
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
      blending: NormalBlending,
      // The flat camera has y growing downward, which turns every triangle over.
      side: DoubleSide,
      transparent: true,
      depthTest: false,
      depthWrite: false,
    });
    const mesh = new Mesh(geometry, material);
    mesh.frustumCulled = false;
    this.scene.add(mesh);
    this.live.push({ mesh, born: now });
  }

  /** Advance every arc to `now` (ms) and draw them; spent ones are removed. */
  render(renderer: WebGLRenderer, now: number): void {
    for (let i = this.live.length - 1; i >= 0; i--) {
      const arc = this.live[i] as Live;
      const age = now - arc.born;
      if (age >= ARC_MS) {
        this.drop(i);
        continue;
      }
      const u = arc.mesh.material.uniforms;
      (u['uHead'] as { value: number }).value = arcHead(age);
      (u['uFade'] as { value: number }).value = arcFade(age);
    }
    if (this.live.length === 0) return;
    renderer.render(this.scene, this.camera);
  }

  clear(): void {
    while (this.live.length > 0) this.drop(0);
  }

  private drop(i: number): void {
    const [arc] = this.live.splice(i, 1);
    if (!arc) return;
    this.scene.remove(arc.mesh);
    arc.mesh.geometry.dispose();
    arc.mesh.material.dispose();
  }
}
