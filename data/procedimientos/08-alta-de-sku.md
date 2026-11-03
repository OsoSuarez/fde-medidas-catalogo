---
titulo: Alta de productos nuevos (SKU)
acceso: supervisor
version: 2026-11
---
# Alta de productos nuevos (SKU)

## Cuándo se hace
- Las altas se procesan 3 veces por semana (lunes, miércoles y viernes), en tandas.
- Se dan de alta alrededor de 200 productos por mes.

## Flujo
1. El proveedor manda la ficha del producto por mail (descripción, código de barras, medidas y peso en sus propias unidades).
2. Assortment pasa los datos a la planilla de altas. El motor de medidas propone las medidas y el peso en cm y kg, con un estado: ok, convertido, revisar o fuera de tema.
3. Lo que queda en "revisar" se manda a medir físicamente antes de seguir.
4. Con la planilla completa se crea un ticket de alta para el equipo de Tecnología.
5. El equipo de Datos Maestros aprueba el ticket. Sin esa aprobación, Tecnología no ejecuta el alta.
6. Tecnología carga el producto en el sistema.

## Quién ve qué
- Assortment ve la ficha completa del proveedor, incluidas las condiciones comerciales.
- Tecnología y Datos Maestros solo necesitan las especificaciones físicas (medidas, peso, código de barras).

## Respaldo de la información
Cada tanda aprobada se guarda en el repositorio de fichas. Si la planilla de altas se pisa o se rompe, se recupera desde ahí y no desde los mails.
