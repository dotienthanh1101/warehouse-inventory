"""API danh mục loại hàng và tải marker ArUco chuẩn."""
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app import catalog_service as service

router = APIRouter(prefix='/api/catalog', tags=['Catalog'])


class ProductFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    # Optional for compatibility; a supplied value must match the marker ID.
    product_code: str | None = Field(default=None, min_length=1, max_length=40)
    product_name: str = Field(min_length=1, max_length=120)


class ProductCreate(ProductFields):
    marker_id: int = Field(ge=1, le=249, strict=True)


class ProductUpdate(ProductFields):
    revision: int = Field(ge=1, strict=True)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1, strict=True)


def call(response, function, *args):
    response.headers['Cache-Control'] = 'no-store'
    try:
        return function(*args)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get('/products')
def list_products(response: Response):
    return call(response, service.list_products)


@router.post('/products', status_code=201)
def create_product(request: ProductCreate, response: Response):
    return call(response, service.create_product, request.marker_id, request.product_code, request.product_name)


@router.put('/products/{marker_id}')
def update_product(marker_id: int, request: ProductUpdate, response: Response):
    return call(response, service.update_product, marker_id, request.revision, request.product_code, request.product_name)


@router.delete('/products/{marker_id}')
def delete_product(marker_id: int, response: Response, revision: int = Query(ge=1)):
    return call(response, service.delete_product, marker_id, revision)


@router.post('/products/{marker_id}/activate')
def activate_product(marker_id: int, request: RevisionRequest, response: Response):
    return call(response, service.activate_product, marker_id, request.revision)


@router.get('/products/{marker_id}/marker.png')
def download_marker(marker_id: int):
    try:
        product = service.get_product(marker_id)
    except ValueError as error:
        raise HTTPException(404, str(error)) from error
    if not product['is_active']:
        raise HTTPException(409, 'Loại hàng đã ngừng sử dụng. Kích hoạt lại trước khi tải nhãn.')
    import cv2
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_250)
    marker = cv2.aruco.generateImageMarker(dictionary, marker_id, 600)
    image = cv2.copyMakeBorder(marker, 100, 100, 100, 100, cv2.BORDER_CONSTANT, value=255)
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise HTTPException(500, 'Không tạo được ảnh nhãn.')
    return Response(content=encoded.tobytes(), media_type='image/png', headers={
        'Cache-Control': 'no-store',
        'Content-Disposition': f'attachment; filename="aruco_4x4_250_id_{marker_id:02d}.png"',
    })
