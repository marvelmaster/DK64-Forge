"""Optional desktop pose interpolation; authored sample endpoints stay exact."""
import numpy as np
from .core.pose_gltf import _rotation_to_quaternion, JointTRS, recompose_trs


def matrix_parts(matrix):
    u, _, vt = np.linalg.svd(matrix[:3,:3])
    rotation = u @ vt
    if np.linalg.det(rotation) < 0:
        u[:,-1] *= -1
        rotation = u @ vt
    return np.asarray(_rotation_to_quaternion(tuple(rotation.flat))), rotation.T @ matrix[:3,:3], matrix[:3,3]


def blend_matrix(a, b, fraction):
    if fraction <= 0:
        return a
    if fraction >= 1:
        return b
    qa, stretch_a, ta = matrix_parts(a)
    qb, stretch_b, tb = matrix_parts(b)
    dot = float(qa@qb)
    if dot < 0:
        qb = -qb; dot = -dot
    if dot > .9995:
        q = qa + (qb-qa)*fraction
        q /= np.linalg.norm(q)
    else:
        angle = np.arccos(np.clip(dot,-1,1))
        q = (qa*np.sin((1-fraction)*angle)+qb*np.sin(fraction*angle))/np.sin(angle)
    result = np.asarray(recompose_trs(JointTRS((0.,0.,0.),tuple(q),(1.,1.,1.),0.,0.))).reshape(4,4)
    result[:3,:3] = result[:3,:3] @ (stretch_a+(stretch_b-stretch_a)*fraction)
    result[:3,3] = ta+(tb-ta)*fraction
    return result
