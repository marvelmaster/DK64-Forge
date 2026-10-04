"""Conservative frustum rejection; never substitutes game portal visibility."""
import numpy as np

def batch_spheres(data):
    points = np.asarray(data.positions)
    centers, radii = [], []
    for batch in data.batches:
        vertices = points[batch.first_vertex:batch.first_vertex+batch.vertex_count]
        center = np.asarray(batch.billboard_center) if batch.billboard_center is not None else (vertices.min(axis=0)+vertices.max(axis=0))/2
        centers.append(center)
        radii.append(np.linalg.norm(vertices-center, axis=1).max(initial=0))
    return np.asarray(centers), np.asarray(radii)

def visible_batches(spheres, mvp):
    centers, radii = spheres
    planes = np.asarray([mvp[3]+sign*mvp[axis] for axis in range(3) for sign in (-1, 1)])
    normals = planes[:, :3]
    return np.all(centers @ normals.T + planes[:, 3] >= -radii[:, None]*np.linalg.norm(normals, axis=1), axis=1)
