#!/usr/bin/env python

"""
This is a script for quick VTK-based visualizations of finite element
computations results.

Examples
--------
  The examples assume that run_tests.py has been run successfully and the
  resulting data files are present.

  - View data in output-tests/test_navier_stokes.vtk.

    $ python resview.py output-tests/test_navier_stokes.vtk

  - Customize the above output:
    plot0: field "p", switch on edges,
    plot1: field "u", surface with opacity 0.4, glyphs scaled by factor 2e-2.

    $ python resview.py output-tests/test_navier_stokes.vtk -f p:e:p0 u:o.4:p1 u:g:f2e-2:p1

  - As above, but glyphs are scaled by the factor determined automatically as
    20% of the minimum bounding box size.

    $ python resview.py output-tests/test_navier_stokes.vtk -f p:e:p0 u:o.4:p1 u:g:f10%:p1

  - View data and take a screenshot.

    $ python resview.py output-tests/test_poisson.vtk -o image.png

  - Take a screenshot without a window popping up.

    $ python resview.py output-tests/test_poisson.vtk -o image.png --off-screen

  - Create animation from output-tests/test_time_poisson.*.vtk.

    $ python resview.py output-tests/test_time_poisson.*.vtk -a mov.mp4

  - Create animation from output-tests/test_hyperelastic.*.vtk,
    set frame rate to 3, plot displacements and mooney_rivlin_stress.

    $ python resview.py output-tests/test_hyperelastic_TL.*.vtk -f u:wu:e:p0 mooney_rivlin_stress:p1 -a mov.mp4 -r 3
"""
from argparse import ArgumentParser, Action, RawDescriptionHelpFormatter
from ast import literal_eval
import numpy as nm
import os.path as osp

import pyvista as pv
from vtk.util.numpy_support import numpy_to_vtk

cache = {}


def get_camera_position(bounds, azimuth, elevation, distance=None, zoom=1.):
    phi, psi = nm.deg2rad(azimuth), nm.deg2rad(elevation)
    bounds = nm.asarray(bounds)

    if distance is not None:
        r = distance / zoom
    else:
        r = max(bounds[1::2] - bounds[::2]) * 2.0 / zoom

    center = (bounds[1::2] + bounds[::2]) * 0.5

    # camera position
    position = (r * nm.cos(phi) * nm.sin(psi),
                r * nm.sin(phi) * nm.sin(psi),
                r * nm.cos(psi))

    # view up
    view_up = (0, 0, 1)
    if abs(elevation) < 5. or abs(elevation) > 175.:
        view_up = (nm.sin(phi), nm.cos(phi), 0)

    return [position, tuple(center), view_up]


def parse_options(opts, separator=':'):
    out = {}
    if opts is None:
        return out

    for v in opts.split(separator):
        if len(v) < 2:
            val = True
        elif v[1:].isalpha():
            val = v[1:]
        elif v[-1] == '%':
            val = ('%', float(v[1:-1]))
        else:
            try:
                val = literal_eval(v[1:])
            except ValueError:
                val = v[1:]

        out[v[0]] = val

    return out


def make_cells_from_conn(conns, convert_to_vtk_type):
    cells, cell_type, offset = [], [], []
    _offset = 0
    for ctype, conn in conns.items():
        nc, np = conn.shape

        aux = nm.empty((nc, np + 1), dtype=int)
        aux[:, 0] = np
        aux[:, 1:] = conn
        cells.append(aux.ravel())

        cell_type.append(nm.full(nc, convert_to_vtk_type[ctype]))
        offset.append(nm.arange(nc) * (np + 1) + _offset)
        _offset += nc

    cells = nm.concatenate(cells)
    cell_type = nm.concatenate(cell_type)
    offset = nm.concatenate(offset)

    return cells, cell_type, offset


def add_mat_id_to_grid(grid, cell_groups):
    val = numpy_to_vtk(cell_groups)
    val.SetName('mat_id')
    grid.GetCellData().AddArray(val)
    return grid


def read_mesh(filenames, step=None, print_info=True, ret_n_steps=False,
              use_cache=True):
    _, ext = osp.splitext(filenames[0])
    if ext in ['.vtk', '.vtu']:
        fstep = 0 if step is None else step
        fname = filenames[fstep]
        key = (fname, fstep)
        if key not in cache or not use_cache:
            cache[key] = pv.UnstructuredGrid(fname)
        mesh = cache[key]
        cache['n_steps'] = len(filenames)
    elif ext in ['.xdmf', '.xdmf3']:
        import meshio
        try:
            from meshio._common import meshio_to_vtk_type

        except ImportError:
            from meshio._vtk_common import meshio_to_vtk_type

        fname = filenames[0]
        key = (fname, step)
        if key not in cache:
            reader = meshio.xdmf.TimeSeriesReader(fname)
            points, _cells = reader.read_points_cells()
            points = nm.asarray(points)
            if points.shape[1] < 3:
                points = nm.pad(points, [(0, 0), (0, 3 - points.shape[1])])
            _dcells = {ct.type: ct.data for ct in _cells}

            cells, cell_type, offset = make_cells_from_conn(
                _dcells, meshio_to_vtk_type,
            )

            if not reader.num_steps:
                grid = pv.UnstructuredGrid(offset, cells, cell_type, points)
                add_mat_id_to_grid(grid, mesh.cmesh.cell_groups)
                cache[(fname, 0)] = grid

            grids = {}
            time = []
            for _step in range(reader.num_steps):
                grid = pv.UnstructuredGrid(offset, cells, cell_type, points)
                t, pd, cd = reader.read_data(_step)
                for dk, dv in pd.items():
                    val = numpy_to_vtk(dv)
                    val.SetName(dk)
                    grid.GetPointData().AddArray(val)

                for dk, dv in cd.items():
                    val = numpy_to_vtk(nm.vstack(dv).squeeze())
                    val.SetName(dk)
                    grid.GetCellData().AddArray(val)

                grids[t] = grid
                time.append(t)

            time.sort()
            for _step, t in enumerate(time):
                cache[(fname, _step)] = grids[t]

            cache[(fname, None)] = cache[(fname, 0)]
            cache['n_steps'] = reader.num_steps

        mesh = cache[key]

    elif ext in ['.h5', '.h5x']:
        vtk_cell_types = {'1_1': 1, '1_2': 3, '2_2': 3, '3_2': 3,
                          '2_3': 5, '2_4': 9, '3_4': 10, '3_8': 12}
        # Custom sfepy format.
        fname = filenames[0]
        key = (fname, step)
        if key not in cache:
            from sfepy.discrete.fem.meshio import MeshIO

            io = MeshIO.any_from_filename(fname)

            mesh = io.read()
            desc = mesh.descs[0]
            nv, dim = mesh.coors.shape

            points = nm.c_[mesh.coors, nm.zeros((nv, 3 - dim))]
            cells, cell_type, offset = make_cells_from_conn(
                {desc: mesh.get_conn(desc)}, vtk_cell_types,
            )

            steps, times, nts = io.read_times()
            if not len(steps):
                grid = pv.UnstructuredGrid(offset, cells, cell_type, points)
                add_mat_id_to_grid(grid, mesh.cmesh.cell_groups)
                cache[(fname, 0)] = grid

            for ii, _step in enumerate(steps):
                grid = pv.UnstructuredGrid(offset, cells, cell_type, points)
                datas = io.read_data(_step)
                for dk, data in datas.items():
                    vval = data.data
                    if 1 < len(data.dofs) < 3:
                        vval = nm.c_[vval,
                                     nm.zeros((len(vval), 3 - len(data.dofs)))]

                    if data.mode == 'vertex':
                        val = numpy_to_vtk(vval)
                        val.SetName(dk)
                        grid.GetPointData().AddArray(val)

                    else:
                        val = numpy_to_vtk(vval[:, 0, :, 0])
                        val.SetName(dk)
                        grid.GetCellData().AddArray(val)

                cache[(fname, ii)] = grid

            cache[(fname, None)] = cache[(fname, 0)]
            cache['n_steps'] = len(steps)

        mesh = cache[key]

    else:
        raise ValueError('unknown file format! (%s)' % ext)

    if print_info:
        arrs = {'s': [], 'v': [], 'o': []}
        for aname in mesh.array_names:
            if len(mesh[aname].shape) == 1 or mesh[aname].shape[1] == 1:
                arrs['s'].append(aname)
            elif mesh[aname].shape[1] == 3:
                arrs['v'].append(aname)
            else:
                arrs['o'].append(aname + '(%d)' % mesh[aname].shape[1])

        step_info = ' (step %d)' % step if step else ''
        print('mesh from %s%s:' % (fname, step_info))
        print('  points:  %d' % mesh.n_points)
        print('  cells:   %d' % mesh.n_cells)
        print('  bounds:  %s' % list(zip(nm.min(mesh.points, axis=0),
                                         nm.max(mesh.points, axis=0))))
        if len(arrs['s']) > 0:
            print('  scalars: %s' % ', '.join(arrs['s']))
        if len(arrs['v']) > 0:
            print('  vectors: %s' % ', '.join(arrs['v']))
        if len(arrs['o']) > 0:
            print('  others:  %s' % ', '.join(arrs['o']))
        print('  steps:   %d' % cache['n_steps'])

    if ret_n_steps:
        return mesh, cache['n_steps']
    else:
        return mesh


def pv_plot(filenames, options, plotter=None, step=None,
            scalar_bar_limits=None, ret_scalar_bar_limits=False,
            step_inc=None, use_cache=True):
    plots = {}
    color = None

    if plotter is None:
        plotter = pv.Plotter()

    fstep = (step if step is not None else options.step)
    if step_inc is not None:
        plotter.clear()
        fstep += step_inc
    if fstep < 0:
        fstep = 0
    if hasattr(plotter, 'resview_n_steps'):
        if fstep >= plotter.resview_n_steps:
            fstep = plotter.resview_n_steps - 1

    mesh, n_steps = read_mesh(filenames, fstep, ret_n_steps=True,
                              use_cache=use_cache)
    steps = {fstep: mesh}

    bbox_sizes = nm.diff(nm.reshape(mesh.bounds, (-1, 2)), axis=1)
    ii = nm.where(bbox_sizes > 0)[0]
    tdim = len(ii)
    if options.position_vector is None:
        if len(ii):
            ipv = ii[-1]

        else:
            ipv = 2
            print('WARNING: zero size mesh!')

        options.position_vector = [0, 0, 0]
        options.position_vector[ipv] = 1.6

    plotter.resview_step, plotter.resview_n_steps = fstep, n_steps

    fields_map = {}
    if len(options.fields_map) > 0:
        for cg, fields in options.fields_map:
            for field in fields.split(','):
                fields_map[field.strip()] = int(cg)

    if len(options.fields) == 0:
        fields = []
        position = 0
        for field in steps[fstep].array_names:
            if field in ['node_groups', 'mat_id']:
                continue

            fval = steps[fstep][field]
            bnds = steps[fstep].bounds
            mesh_size = (nm.array(bnds[1::2]) - nm.array(bnds[::2])).max()
            is_vector_field = len(fval.shape) > 1
            is_point_field = fval.shape[0] == steps[fstep].n_points
            if is_vector_field and is_point_field:
                scale = mesh_size * 0.15 / nm.linalg.norm(fval, axis=1).max()
                fields.append((field, 'vw:p%d' % position))
                fields.append((field, 'vs:o.4:p%d' % position))
                fields.append((field, 'g:f%e:p%d' % (scale, position)))
            else:
                fields.append((field, 'p%d' % position))

            position += 1

        if len(fields) == 0:
            fields.append(('mat_id', 'p0'))
    else:
        fields = options.fields

    plot_id = 0

    scalar_bars = {}
    for field, fopts in fields:
        opts = parse_options(fopts)
        plot_info = []

        if field == '0':
            field = None
            color = 'white'

        if field == '1':
            field = None
            color = 'black'

        if 's' in opts and step is None:  # plot data from a given step
            fstep = opts['s']

        if fstep not in steps:
            steps[fstep] = read_mesh(filenames, step=fstep,
                                     use_cache=use_cache)

        pipe = [steps[fstep].copy()]

        if field in fields_map:  # subregion
            mat_val = fields_map[field]
        elif 'm' in opts:
            mat_val = opts['m']
        else:
            mat_val = None

        if mat_val:
            if isinstance(mat_val, int):
                mat_val = [mat_val, mat_val]

            pipe.append(pipe[-1].threshold(value=mat_val,
                        scalars='mat_id', preference='cell'))

        if 'r' in opts:  # recalculate cell data to point data
            pipe.append(pipe[-1].cell_data_to_point_data())

        opacity = opts.get('o', options.opacity)  # mesh opacity
        show_edges = opts.get('e', options.show_edges)  # edge visibility
        style = {'s': 'surface',
                 'w': 'wireframe',
                 'p': 'points'}[opts.get('v', 's')]  # set style

        warp = opts.get('w', options.warp)  # warp mesh
        factor = opts.get('f', options.factor)
        if isinstance(factor, tuple):
            ws = nm.diff(nm.reshape(pipe[-1].bounds, (-1, 2)), axis=1)
            size = ws[ws > 0.0].min()
            fmax = nm.abs(pipe[-1][field]).max()
            factor = 0.01 * float(factor[1]) * size / fmax

        if warp:
            field_data = pipe[-1][warp]
            if field_data.ndim == 1:
                field_data.shape = (-1, 1)
            nc = field_data.shape[1]
            if nc == 1:  # Warp by scalar.
                pipe.append(pipe[-1].copy())
                pipe[-1].points[:, 2] += field_data[:, 0] * factor

            elif nc == 3:
                pipe.append(pipe[-1].copy())
                pipe[-1].points += field_data * factor

            else:
                raise ValueError('warp mesh: scalar or vector field required!')

            plot_info.append('warp=%s, factor=%.2e' % (warp, factor))

        position = opts.get('p', 0)  # determine plotting slot
        bnds = pipe[-1].bounds
        if 'p' in opts:
            size = nm.array(bnds[1::2]) - nm.array(bnds[::2])
            pipe.append(pipe[-1].copy())
            shift = position * size * nm.array(options.position_vector)
            pipe[-1].translate(shift)

        if opts.get('l', options.outline):  # outline
            plotter.add_mesh(pipe[-1].outline(), color='k')

        scalar = field
        scalar_label = scalar
        is_vector_field = field is not None and len(pipe[-1][field].shape) > 1
        is_point_field = (field is not None and
                          pipe[-1][field].shape[0] == pipe[-1].n_points)
        if is_vector_field:
            field_data = pipe[-1][field]
            scalar = field + '_magnitude'
            scalar_label = f'|{field}|'
            pipe[-1][scalar] = nm.linalg.norm(field_data, axis=1)

        if 'g' in opts and is_vector_field and is_point_field:  # glyphs
            pipe[-1][field] *= factor
            pipe[-1].set_active_vectors(field)
            pipe.append(pipe[-1].arrows)
            style = ''
            plot_info.append('glyphs=%s, factor=%.2e' % (field, factor))
        elif 'c' in opts and is_vector_field:  # select field component
            comp = opts['c']
            scalar = field + '_%d' % comp
            pipe[-1][scalar] = field_data[:, comp]
        elif 't' in opts:  # streamlines
            npts = opts.get('t')
            if npts is True:
                npts = 20

            if is_vector_field:
                sl_vector = field
                sl_pipe = pipe[-1]
            else:
                sl_vector = 'gradient'
                sl_pipe = pipe[-1].compute_derivative(scalars=field)

            cmin, cmax = sl_pipe.bounds[::2], sl_pipe.bounds[1::2]
            if tdim == 2:
                streamlines = sl_pipe.streamlines(vectors=sl_vector,
                                                  pointa=cmin, pointb=cmax,
                                                  n_points=npts,
                                                  max_time=1e12)

            else:
                radius = 0.5 * nm.linalg.norm(nm.array(cmax) - nm.array(cmin))
                streamlines = sl_pipe.streamlines(vectors=sl_vector,
                                                  source_radius=radius,
                                                  n_points=npts,
                                                  max_time=1e12)

            pipe.append(streamlines)

        plotter.add_mesh(pipe[-1], scalars=scalar, color=color,
                         style=style, show_edges=show_edges,
                         opacity=opacity,
                         cmap=options.color_map,
                         show_scalar_bar=False, label=scalar_label)

        bnds = pipe[-1].bounds
        if position not in plots:
            plots[position] = []

        plot_info = ':' + ','.join(plot_info) if len(plot_info) > 0 else ''
        plot_info = '%s(step %d)%s' % (scalar, fstep, plot_info)
        plots[position].append(((bnds[::2], bnds[1::2]), plot_info))

        if options.show_scalar_bars and scalar:
            if scalar not in scalar_bars:
                scalar_bars[scalar_label] = []

            field_data = pipe[-1][scalar]
            limits = (nm.min(field_data), nm.max(field_data))
            scalar_bars[scalar_label].append((limits, plotter.mapper,
                                              position))

        plot_id += 1

    if options.show_scalar_bars:
        if scalar_bar_limits is None:
            scalar_bar_limits = {}
            for k, vs in scalar_bars.items():
                limits = (nm.min([v[0] for v, _, _ in vs]),
                          nm.max([v[1] for v, _, _ in vs]))
                scalar_bar_limits[k] = limits

        width, height = options.scalar_bar_size
        position_x, position_y, shift_x, shift_y = options.scalar_bar_position
        nslots = len(scalar_bars)
        for k, vs in scalar_bars.items():
            clim = scalar_bar_limits[k]
            for _, mapper, _ in vs:
                mapper.scalar_range = clim
            _, mapper, slot = vs[0]

            slot_x = (nslots - slot - 1) if shift_x < 0 else slot
            x_pos = position_x + slot_x * width * shift_x
            slot_y = (nslots - slot - 1) if shift_y < 0 else slot
            y_pos = position_y + slot_y * height * shift_y

            plotter.add_scalar_bar(title=k,
                                   position_x=x_pos, position_y=y_pos,
                                   width=width, height=height,
                                   n_labels=2, mapper=mapper)

    if options.show_labels and len(plots) > 1:
        labels, points = [], []
        for k, v in plots.items():
            bnds = (nm.min(nm.array([iv[0][0] for iv in v]), axis=0),
                    nm.max(nm.array([iv[0][1] for iv in v]), axis=0))
            labels.append('plot:%d' % k)
            size = bnds[1] - bnds[0]
            olpos = options.label_position
            points.append(bnds[0] + nm.array(olpos[:3]) * size * olpos[3])

        plotter.add_point_labels(nm.array(points), labels)

    for k, v in plots.items():
        print('plot %d: %s' % (k, '; '.join(iv[1] for iv in v)))

    if ret_scalar_bar_limits:
        return plotter, scalar_bar_limits
    else:
        return plotter


class OptsToListAction(Action):
    separator = '='

    def __call__(self, parser, namespace, values, option_string=None):
        out = []
        for item in values:
            s = item.split(self.separator, 1)
            out.append((s[0].strip(), s[1].strip() if len(s) > 1 else None))

        setattr(namespace, self.dest, out)


class FieldOptsToListAction(OptsToListAction):
    separator = ':'


class StoreNumberAction(Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, literal_eval(values))


helps = {
    'fields':
        'fields to plot, options separated by ":" are possible:\n'
        '"cX" - plot only Xth field component; '
        '"e" - print edges; '
        '"fX" - scale factor for warp/glyphs, see --factor option; '
        '"g - glyphs (for vector fields only), scale by factor; '
        '"tX" - plot X streamlines, gradient employed for scalar fields; '
        '"mX" - plot cells with mat_id=X; '
        '"oX" - set opacity to X; '
        '"pX" - plot in slot X; '
        '"r" - recalculate cell data to point data; '
        '"sX" - plot data in step X; '
        '"vX" - plotting style: s=surface, w=wireframe, p=points; '
        '"wX" - warp mesh by vector field X, scale by factor',
    'fields_map':
        'map fields and cell groups, e.g. 1:u1,p1 2:u2,p2',
    'outline':
        'plot mesh outline',
    'warp':
        'warp mesh by vector field',
    'factor':
        'scaling factor for mesh warp and glyphs.'
        ' Append "%%" to scale relatively to the minimum bounding box size.',
    'edges':
        'plot cell edges',
    'opacity':
        'set opacity [default: %(default)s]',
    'color_map':
        'set color_map, e.g. hot, cool, bone, etc. [default: %(default)s]',
    'axes_options':
        'options for directional axes, e.g. xlabel="z1" ylabel="z2",'
        ' zlabel="z3"',
    'no_axes':
        'hide orientation axes',
    'no_scalar_bars':
        'hide scalar bars',
    'position_vector':
        'define positions of plots [default: "0, 0, 1.6"]',
    'view':
        'camera azimuth, elevation angles, and optionally zoom factor'
        ' [default: "225,75,0.9"]',
    'animation':
        'create animation, mp4 file type supported',
    'framerate':
        'set framerate for animation',
    'screenshot':
        'save screenshot to file',
    'off_screen':
        'off screen plots, e.g. when screenshotting',
    'no_labels':
        'hide plot labels',
    'label_position':
        'define position of plot labels [default: "-1, -1, 0, 0.2"]',
    'scalar_bar_size':
        'define size of scalar bars [default: "0.15, 0.05"]',
    'scalar_bar_position':
        'define position of scalar bars [default: "0.8, 0.02, 0, 1.5"]',
    'step':
        'select data in a given time step',
    '2d_view':
        '2d view of XY plane',
}


def main():
    parser = ArgumentParser(description=__doc__,
                            formatter_class=RawDescriptionHelpFormatter)
    parser.add_argument('-f', '--fields', metavar='field_spec',
                        action=FieldOptsToListAction, nargs="+", dest='fields',
                        default=[], help=helps['fields'])
    parser.add_argument('--fields-map', metavar='map',
                        action=FieldOptsToListAction, nargs="+",
                        dest='fields_map',
                        default=[], help=helps['fields_map'])
    parser.add_argument('-s', '--step', metavar='step',
                        action=StoreNumberAction, dest='step',
                        default=0, help=helps['step'])
    parser.add_argument('-l', '--outline',
                        action='store_true', dest='outline',
                        default=False, help=helps['outline'])
    parser.add_argument('-e', '--edges',
                        action='store_true', dest='show_edges',
                        default=False, help=helps['edges'])
    parser.add_argument('-w', '--warp', metavar='field',
                        action='store', dest='warp',
                        default=None, help=helps['warp'])
    parser.add_argument('--factor', metavar='factor',
                        action=StoreNumberAction, dest='factor',
                        default=1., help=helps['factor'])
    parser.add_argument('--opacity', metavar='opacity',
                        action=StoreNumberAction, dest='opacity',
                        default=1., help=helps['opacity'])
    parser.add_argument('--color-map', metavar='cmap',
                        action='store', dest='color_map',
                        default='viridis', help=helps['color_map'])
    parser.add_argument('--axes-options', metavar='options',
                        action=OptsToListAction, nargs="+",
                        dest='axes_options',
                        default=[], help=helps['axes_options'])
    parser.add_argument('--no-axes',
                        action='store_false', dest='axes_visibility',
                        default=True, help=helps['no_axes'])
    parser.add_argument('--position-vector', metavar='position_vector',
                        action=StoreNumberAction, dest='position_vector',
                        default=None, help=helps['position_vector'])
    parser.add_argument('--no-labels',
                        action='store_false', dest='show_labels',
                        default=True, help=helps['no_labels'])
    parser.add_argument('--label-position', metavar='position',
                        action=StoreNumberAction, dest='label_position',
                        default=[-1, -1, 0, 0.2], help=helps['label_position'])
    parser.add_argument('--no-scalar-bars',
                        action='store_false', dest='show_scalar_bars',
                        default=True, help=helps['no_scalar_bars'])
    parser.add_argument('--scalar-bar-size', metavar='size',
                        action=StoreNumberAction, dest='scalar_bar_size',
                        default=[0.15, 0.05],
                        help=helps['scalar_bar_size'])
    parser.add_argument('--scalar-bar-position', metavar='position',
                        action=StoreNumberAction, dest='scalar_bar_position',
                        default=[0.8, 0.02, 0, 1.5],
                        help=helps['scalar_bar_position'])
    parser.add_argument('-v', '--view', metavar='position',
                        action=StoreNumberAction, dest='camera',
                        default=[225, 75, 0.9], help=helps['view'])
    parser.add_argument('-a', '--animation', metavar='output_file',
                        action='store', dest='anim_output_file',
                        default=None, help=helps['animation'])
    parser.add_argument('-r', '--frame-rate', metavar='rate',
                        action=StoreNumberAction, dest='framerate',
                        default=2.5, help=helps['framerate'])
    parser.add_argument('-o', '--screenshot', metavar='output_file',
                        action='store', dest='screenshot',
                        default=None, help=helps['screenshot'])
    parser.add_argument('--off-screen',
                        action='store_true', dest='off_screen',
                        default=False, help=helps['off_screen'])
    parser.add_argument('-2', '--2d-view',
                        action='store_true', dest='view_2d',
                        default=False, help=helps['2d_view'])

    parser.add_argument('filenames', nargs='+')
    options = parser.parse_args()

    pv.set_plot_theme("document")
    plotter = pv.Plotter(off_screen=options.off_screen)

    if options.anim_output_file:
        _, n_steps = read_mesh(options.filenames, ret_n_steps=True)
        # dry run
        scalar_bar_limits = None
        if options.axes_visibility:
            plotter.add_axes(**dict(options.axes_options))
        for step in range(n_steps):
            plotter, sb_limits = pv_plot(options.filenames, options,
                                         plotter=plotter, step=step,
                                         ret_scalar_bar_limits=True)
            if scalar_bar_limits is None:
                scalar_bar_limits = {k: [] for k in sb_limits.keys()}

            for k, v in sb_limits.items():
                scalar_bar_limits[k].append(v)

        if options.camera:
            zoom = options.camera[2] if len(options.camera) > 2 else 1.
            cpos = get_camera_position(plotter.bounds,
                                       options.camera[0], options.camera[1],
                                       zoom=zoom)
            plotter.set_position(cpos[0])
            plotter.set_focus(cpos[1])
            plotter.set_viewup(cpos[2])

        anim_filename = options.anim_output_file
        plotter.open_movie(anim_filename, options.framerate)
        plotter.show(auto_close=False)

        for k in scalar_bar_limits.keys():
            lims = scalar_bar_limits[k]
            clim = (nm.min([v[0] for v in lims]),
                    nm.max([v[1] for v in lims]))
            scalar_bar_limits[k] = clim

        # plot frames
        for step in range(n_steps):
            plotter.clear()
            plotter = pv_plot(options.filenames, options, plotter=plotter,
                              step=step, scalar_bar_limits=scalar_bar_limits)
            if options.axes_visibility:
                plotter.add_axes(**dict(options.axes_options))

            plotter.write_frame()

        plotter.close()
    else:
        plotter = pv_plot(options.filenames, options, plotter=plotter)
        if options.axes_visibility:
            plotter.add_axes(**dict(options.axes_options))

        plotter.add_key_event(
            'Prior', lambda: pv_plot(options.filenames,
                                     options,
                                     step=plotter.resview_step,
                                     step_inc=-1,
                                     plotter=plotter)
        )
        plotter.add_key_event(
            'Next', lambda: pv_plot(options.filenames,
                                    options,
                                    step=plotter.resview_step,
                                    step_inc=1,
                                    plotter=plotter)
        )

        if options.view_2d:
            plotter.view_xy()
            plotter.show(screenshot=options.screenshot)
        else:
            if options.camera:
                zoom = options.camera[2] if len(options.camera) > 2 else 1.
                cpos = get_camera_position(plotter.bounds,
                                           options.camera[0],
                                           options.camera[1],
                                           zoom=zoom)
            else:
                cpos = None

            plotter.show(cpos=cpos, screenshot=options.screenshot)


if __name__ == '__main__':
    main()
