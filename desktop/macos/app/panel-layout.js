const clamp = (value, minimum, maximum) =>
  Math.max(minimum, Math.min(value, maximum));

export const panelLayout = ({
  anchor,
  viewportWidth,
  viewportHeight,
  contentHeight = 0,
}) => {
  const scale = Number(anchor.scale) > 0 ? Number(anchor.scale) : 1;
  const margin = 10;
  const width = Math.min(392, Math.max(0, viewportWidth - margin * 2));
  const maxHeight = Math.max(0, viewportHeight - margin * 2);
  const height = Math.min(contentHeight, maxHeight);
  return {
    width,
    maxHeight,
    left: clamp(Number(anchor.x) / scale - width / 2, margin, viewportWidth - width - margin),
    top: clamp(Number(anchor.y) / scale + 7, margin, viewportHeight - height - margin),
  };
};
