---
titulo: Transferencias a tiendas (TR) y faltantes
acceso: operario
version: 2026-10
---
# Transferencias a tiendas (TR) y faltantes

## Qué es una TR
Una transferencia (TR) es el pedido de reposición que el CD prepara para una tienda (dark store). Cada TR tiene un número, una tienda de destino y una lista de productos con cantidades.

## Preparación (picking)
1. Las TR se liberan en olas por horario de salida del camión.
2. El preparador escanea cada producto y cantidad; el sistema no deja cerrar una línea con un producto distinto al pedido.
3. Si en la ubicación no hay stock suficiente, el preparador marca la línea como "corta" y sigue con la siguiente. No se reemplaza por otro producto.

## Faltantes en la TR
- Un faltante es la diferencia entre lo pedido y lo preparado. Se informa automáticamente a la tienda cuando se cierra la TR.
- Si el faltante es por stock que figura en sistema pero no está en la ubicación, el preparador avisa al equipo de inventario para hacer un conteo de esa ubicación en el día.

## Cuando la tienda reclama diferencias
1. La tienda tiene 24 horas desde la recepción para reclamar diferencias de una TR.
2. El reclamo se revisa contra el registro de escaneo del picking y la foto del pallet al despachar.
3. Si el registro confirma que se despachó, el reclamo se deriva al equipo de la tienda para recuento. Si no, se repone en la próxima TR.
