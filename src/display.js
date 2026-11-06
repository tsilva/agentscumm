// Observe the default framebuffer's draw geometry, not the colors of a scene.
// ScummVM clips its game and cursor to the game draw rectangle, while GUI
// overlays may draw across the whole output. Offscreen textures are excluded.
export function installDisplayObserver() {
  const frames = new WeakMap();
  for (const prototype of [globalThis.WebGLRenderingContext?.prototype, globalThis.WebGL2RenderingContext?.prototype]) {
    if (!prototype) continue;
    const onscreen = gl => gl.canvas?.id === 'canvas' && gl.getParameter(gl.FRAMEBUFFER_BINDING) === null;
    const clear = prototype.clear;
    prototype.clear = function(mask) {
      const result = clear.call(this, mask);
      if ((mask & this.COLOR_BUFFER_BIT) && onscreen(this) && !this.isEnabled(this.SCISSOR_TEST)) frames.delete(this);
      return result;
    };
    for (const name of ['drawArrays', 'drawElements', 'drawArraysInstanced', 'drawElementsInstanced', 'blitFramebuffer']) {
      const draw = prototype[name];
      if (!draw) continue;
      prototype[name] = function(...args) {
        const result = draw.apply(this, args);
        if (!onscreen(this)) return result;
        const width = this.drawingBufferWidth, height = this.drawingBufferHeight;
        const viewport = this.getParameter(this.VIEWPORT);
        const clip = this.isEnabled(this.SCISSOR_TEST) ? this.getParameter(this.SCISSOR_BOX) : viewport;
        if (!viewport || !clip || width <= 0 || height <= 0) return result;
        let left = Math.max(0, viewport[0], clip[0]), bottom = Math.max(0, viewport[1], clip[1]);
        let right = Math.min(width, viewport[0] + viewport[2], clip[0] + clip[2]);
        let top = Math.min(height, viewport[1] + viewport[3], clip[1] + clip[3]);
        if (right <= left || top <= bottom) return result;
        const previous = frames.get(this);
        if (previous?.canvasWidth === width && previous?.canvasHeight === height) {
          left = Math.min(left, previous.x); right = Math.max(right, previous.x + previous.width);
          bottom = Math.min(bottom, height - previous.y - previous.height); top = Math.max(top, height - previous.y);
        }
        if (right > left && top > bottom) {
          const bounds = { x: left, y: height - top, width: right - left, height: top - bottom,
            canvasWidth: width, canvasHeight: height };
          frames.set(this, bounds);
          this.canvas.__agentscummDisplayBounds = bounds;
        }
        return result;
      };
    }
  }
}

// Return CSS geometry in the screenshot's coordinate space, including iframe
// offsets. Keep the original canvas bounds for MCP input and older viewers.
export function displayBounds(canvas, canvasBounds) {
  const bounds = canvas.__agentscummDisplayBounds;
  if (!bounds || bounds.canvasWidth !== canvas.width || bounds.canvasHeight !== canvas.height) return canvasBounds;
  return { x: canvasBounds.x + bounds.x * canvasBounds.width / canvas.width,
    y: canvasBounds.y + bounds.y * canvasBounds.height / canvas.height,
    width: bounds.width * canvasBounds.width / canvas.width,
    height: bounds.height * canvasBounds.height / canvas.height };
}
